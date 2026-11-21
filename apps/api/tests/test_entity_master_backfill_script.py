"""The operator entry point for the entity-master backfill (scripts/backfill_entity_master.py).

Found 2026-11-21: the deployed environment had the entity master on, 121 companies and 0
legal entities, because the backfill is (by design) called by nothing in ``app/``. These
tests pin the script: a dry run writes nothing, a real run links every row in bounded,
committed passes, a second run links nothing, and the flag off is refused.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models.company import Company
from app.models.legal_entity import LegalEntity

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_entity_master.py"
_spec = importlib.util.spec_from_file_location("backfill_entity_master", SCRIPT)
assert _spec and _spec.loader
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


@pytest.fixture
async def factory():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True,
                                 poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        for ticker, exchange, name in (("POWL", "NASDAQ", "Powell Industries"),
                                       ("CFR", "SIX", "Richemont"),
                                       ("NKT", "CPH", "NKT A/S"),
                                       ("PRY", "MIL", "Prysmian")):
            s.add(Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name,
                          status="new", created_at=datetime(2026, 3, 1, tzinfo=UTC)))
        await s.commit()
    yield maker
    await engine.dispose()


def _cfg(on: bool = True) -> Settings:
    return Settings(v3_entity_master_enabled=on)  # type: ignore[call-arg]


async def _counts(maker):  # noqa: ANN001, ANN202
    async with maker() as s:
        entities = (await s.execute(select(func.count()).select_from(LegalEntity))).scalar_one()
        unlinked = (await s.execute(select(func.count()).select_from(Company).where(
            Company.legal_entity_id.is_(None)))).scalar_one()
    return entities, unlinked


async def test_a_dry_run_reports_and_writes_nothing(factory) -> None:  # noqa: ANN001
    out = await script.run(factory, _cfg(), dry_run=True, batch=200, max_batches=5)
    assert out["dry_run"] is True and out["passes"] == 1
    assert out["unlinked_before"] == 4 and out["linked"] == 4
    assert await _counts(factory) == (0, 4)


async def test_a_run_links_every_row_in_bounded_passes(factory) -> None:  # noqa: ANN001
    out = await script.run(factory, _cfg(), dry_run=False, batch=3, max_batches=5)
    assert out["passes"] == 2 and out["linked"] == 4
    assert out["unlinked_before"] == 4 and out["unlinked_after"] == 0
    assert await _counts(factory) == (4, 0)
    again = await script.run(factory, _cfg(), dry_run=False, batch=3, max_batches=5)
    assert again["linked"] == 0 and again["unlinked_before"] == 0
    assert await _counts(factory) == (4, 0)


async def test_max_batches_bounds_the_run(factory) -> None:  # noqa: ANN001
    out = await script.run(factory, _cfg(), dry_run=False, batch=1, max_batches=2)
    assert out["passes"] == 2 and out["linked"] == 2 and out["unlinked_after"] == 2


async def test_the_flag_off_is_refused(monkeypatch) -> None:  # noqa: ANN001
    from app.core import config

    monkeypatch.setattr(config.settings, "v3_entity_master_enabled", False)
    assert await script.main([]) == 2
