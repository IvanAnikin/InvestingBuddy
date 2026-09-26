"""V3.19.7 — peers the platform does not hold, discovered and VERIFIED.

V3.18 KNOWN LIMITATION 3: MP Materials' "peers" were copper and steel producers because
those were the companies on file. Now, behind V3_DYNAMIC_PEER_DISCOVERY_ENABLED, one
bounded search proposes peers and each must pass the SAME listing verification a
discovery candidate passes.

MUTATION GUARD (unverified peer): a proposed peer whose listing does not verify is never
returned.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.deepseek.transport import DeepSeekResponse
from app.models.company import Company
from app.services.agent_tools import peers
from app.services.discovery import identity as identity_mod


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


class _Transport:
    search_tool_name = "web_search"

    async def investigate_with_search(self, **_kw):
        return DeepSeekResponse(text=json.dumps({"companies": [
            {"legal_name": "Lynas Rare Earths Ltd", "ticker": "LYC", "exchange": "ASX",
             "listing_source_url": "https://lynasrareearths.com/investors",
             "why": "a rare earths producer outside China"},
            {"legal_name": "Phantom Magnets Inc", "ticker": "PHM", "exchange": "Nasdaq",
             "listing_source_url": "https://phantom.example/ir", "why": "rare earths"},
        ]}), finish_reason="stop")


async def _fake_verify(lead, *, cfg=None, fetcher=None, max_fetches=2):
    verified = lead.ticker == "LYC"
    return identity_mod.IdentityOutcome(
        lead=lead, status="verified" if verified else "rejected", ticker=lead.ticker,
        exchange=identity_mod.normalise_venue(lead.exchange_raw), name=lead.name,
        rejection_reason=None if verified else "no_listing_evidence",
        listing_source={"url": lead.listing_source_url, "tier": "issuer"} if verified else None,
    )


async def _subject(session):
    company = Company(id=uuid.uuid4(), ticker="MP", exchange="US", name="MP Materials Corp",
                      industry="Metals & Mining", status="new")
    session.add(company)
    session.add(Company(id=uuid.uuid4(), ticker="FCX", exchange="US",
                        name="Freeport-McMoRan", industry="Metals & Mining", status="new"))
    await session.flush()
    return company


async def test_verified_external_peer_is_added_with_its_basis(session, monkeypatch):
    subject = await _subject(session)
    monkeypatch.setattr("app.services.agents.routing.research_provider_for",
                        lambda cfg: SimpleNamespace(transport=_Transport(), provider_id="ds",
                                                    max_output_tokens=4000))
    monkeypatch.setattr(identity_mod, "verify_identity", _fake_verify)
    context = SimpleNamespace(session=session,
                              cfg=SimpleNamespace(v3_dynamic_peer_discovery_enabled=True))
    payload = await peers._get_peer_set(
        context, peers.validate_get_peer_set({"company_id": str(subject.id),
                                              "commodity": "rare_earths"}))
    external = [i for i in payload["items"] if i["commodity_basis"] == "external_discovery"]
    assert [i["ticker"] for i in external] == ["LYC"]
    assert external[0]["shares_commodity"] is True
    assert external[0]["financials"] == "financials_not_comparable_here"
    assert "listing verified" in external[0]["basis"]
    assert payload["contains_untrusted_content"] is True


async def test_unverified_peer_rejected(session, monkeypatch):
    """MUTATION GUARD: Phantom Magnets' listing does not verify, so it is never a peer."""
    subject = await _subject(session)
    monkeypatch.setattr("app.services.agents.routing.research_provider_for",
                        lambda cfg: SimpleNamespace(transport=_Transport(), provider_id="ds",
                                                    max_output_tokens=4000))
    monkeypatch.setattr(identity_mod, "verify_identity", _fake_verify)
    context = SimpleNamespace(session=session,
                              cfg=SimpleNamespace(v3_dynamic_peer_discovery_enabled=True))
    payload = await peers._get_peer_set(
        context, peers.validate_get_peer_set({"company_id": str(subject.id)}))
    assert "PHM" not in {i["ticker"] for i in payload["items"]}
    assert any("could not have their listing verified" in g for g in payload["gaps"])


async def test_flag_off_is_the_v3_18_behaviour(session):
    subject = await _subject(session)
    payload = await peers._get_peer_set(
        SimpleNamespace(session=session, cfg=SimpleNamespace()),
        peers.validate_get_peer_set({"company_id": str(subject.id)}))
    assert all(i["commodity_basis"] != "external_discovery" for i in payload["items"])
