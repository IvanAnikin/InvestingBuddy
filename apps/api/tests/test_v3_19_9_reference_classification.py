"""V3.19.9 — a European issuer is researched with its industry's methodology.

Kering and Pandora were researched with the GENERIC methodology (no luxury playbook, no
industry questions) because classification had only one source — a SEC SIC code — and
European issuers have none, although the platform's own curated registry classifies
them. The registry, or an industry a discovery run VERIFIED, is now a labelled T5
reference, weaker than any regulator code.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.services.classification import service as cls


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


async def test_a_curated_european_issuer_gets_its_industry(session, monkeypatch):
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    kering = Company(id=uuid.uuid4(), ticker="KER", exchange="PA", name="Kering SA",
                     status="new")
    session.add(kering)
    await session.flush()
    result = await cls.ensure_company_classification(session, kering)
    assert result.industry == "Luxury Goods"
    assert result.tier == "T5_api_aggregator"
    assert any("curated research registry" in n for n in result.notes)


async def test_a_dynamically_discovered_issuer_uses_its_verified_theme(session, monkeypatch):
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    run = DiscoveryRun(id=uuid.uuid4(), status="completed", provider_name="free_real",
                       mode="thesis", universe_source="thesis_generated")
    session.add(run)
    session.add(DiscoveryCandidate(
        id=uuid.uuid4(), discovery_run_id=run.id, ticker="MEX", exchange="PA",
        # The shape verify_industry writes: every pass carries what was matched.
        thesis_match_json={"theme": "luxury_goods", "v319": {"constraint_results": [
            {"key": "industry", "status": "pass",
             "value": {"matched": ["luxury_goods"], "direct": ["luxury_goods"],
                       "exposure": "direct"}}]}},
    ))
    company = Company(id=uuid.uuid4(), ticker="MEX", exchange="PA", name="Maison Exemple",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.industry == "Luxury Goods"
    assert any("verified its industry" in n for n in result.notes)


async def test_an_unverified_theme_is_not_a_classification(session, monkeypatch):
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    run = DiscoveryRun(id=uuid.uuid4(), status="completed", provider_name="free_real",
                       mode="thesis", universe_source="thesis_generated")
    session.add(run)
    session.add(DiscoveryCandidate(
        id=uuid.uuid4(), discovery_run_id=run.id, ticker="UNV", exchange="PA",
        thesis_match_json={"theme": "luxury_goods", "v319": {"constraint_results": [
            {"key": "industry", "status": "unknown"}]}},
    ))
    company = Company(id=uuid.uuid4(), ticker="UNV", exchange="PA", name="Unverified SA",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.industry is None


async def test_a_regulator_code_still_wins(session, monkeypatch):
    async def sec(ticker, exchange):  # noqa: ANN001, ANN202
        return "1000", "Metal Mining", None

    monkeypatch.setattr(cls, "_fetch_sec_classification", sec)
    company = Company(id=uuid.uuid4(), ticker="MP", exchange="US", name="MP Materials",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.tier == "T2_regulator_or_gov"


async def test_a_verified_material_classifies_a_critical_materials_discovery(
    session, monkeypatch
):
    """V3.19.12 — read in production: Pensana (LSE), discovered for a critical-materials
    query and verified from its own website as a rare-earth developer, was researched
    UNCLASSIFIED because the run's theme ("critical_materials") declares no industry.
    The verified matched terms classify it."""
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    run = DiscoveryRun(id=uuid.uuid4(), status="completed", provider_name="free_real",
                       mode="thesis", universe_source="thesis_generated")
    session.add(run)
    session.add(DiscoveryCandidate(
        id=uuid.uuid4(), discovery_run_id=run.id, ticker="PRE", exchange="LSE",
        thesis_match_json={"theme": "critical_materials", "v319": {"constraint_results": [
            {"key": "industry", "status": "pass",
             "value": {"matched": ["critical_materials", "rare_earths"],
                       "direct": ["critical_materials", "rare_earths"],
                       "exposure": "direct"}}]}},
    ))
    company = Company(id=uuid.uuid4(), ticker="PRE", exchange="LSE", name="Pensana Plc",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.industry == "Mining"
    assert result.sector == "Materials"


def test_the_mining_classification_selects_the_mining_playbook():
    from app.services.playbooks import industries  # noqa: F401 - registers playbooks
    from app.services.playbooks.registry import select

    chosen = select(sector="Materials", industry="Mining")
    assert any("mining" in p.playbook_id for p in chosen.playbooks), chosen.reason


async def test_an_indirect_term_never_classifies_a_company(session, monkeypatch):
    """A miner that "supplies data centres" is not a data-centre company."""
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    run = DiscoveryRun(id=uuid.uuid4(), status="completed", provider_name="free_real",
                       mode="thesis", universe_source="thesis_generated")
    session.add(run)
    session.add(DiscoveryCandidate(
        id=uuid.uuid4(), discovery_run_id=run.id, ticker="IND", exchange="LSE",
        thesis_match_json={"theme": "critical_materials", "v319": {"constraint_results": [
            {"key": "industry", "status": "pass",
             "value": {"matched": ["ai_infrastructure", "rare_earths"],
                       "direct": ["rare_earths"], "exposure": "direct"}}]}},
    ))
    company = Company(id=uuid.uuid4(), ticker="IND", exchange="LSE", name="Ind Plc",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.industry == "Mining"


async def test_the_run_theme_classifies_only_when_directly_matched(session, monkeypatch):
    async def no_sec(ticker, exchange):  # noqa: ANN001, ANN202
        return None, None, "venue not SEC-eligible"

    monkeypatch.setattr(cls, "_fetch_sec_classification", no_sec)
    run = DiscoveryRun(id=uuid.uuid4(), status="completed", provider_name="free_real",
                       mode="thesis", universe_source="thesis_generated")
    session.add(run)
    session.add(DiscoveryCandidate(
        id=uuid.uuid4(), discovery_run_id=run.id, ticker="GAL", exchange="AU",
        thesis_match_json={"theme": "semiconductors", "v319": {"constraint_results": [
            {"key": "industry", "status": "pass",
             "value": {"matched": ["gallium"], "direct": ["gallium"],
                       "exposure": "direct"}}]}},
    ))
    company = Company(id=uuid.uuid4(), ticker="GAL", exchange="AU", name="Gal Ltd",
                      status="new")
    session.add(company)
    await session.flush()
    result = await cls.ensure_company_classification(session, company)
    assert result.industry == "Mining"
