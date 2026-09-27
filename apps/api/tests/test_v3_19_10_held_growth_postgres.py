"""V3.19.10 on a real PostgreSQL: growth from a held company's own primary facts.

Only validated, active, explicitly GROUP-scope annual (or split-year) revenue from a
primary document counts. An unknown scope is never promoted to group, a segment is not
the company, and a media document is not a filing. Skipped without
``V3_TEST_POSTGRES_URL``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.discovery.pipeline import growth_from_held_facts

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head"
)


@pytest.fixture
async def factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _company(db) -> Company:  # noqa: ANN001
    company = Company(id=uuid.uuid4(), ticker=f"G{uuid.uuid4().hex[:5]}".upper(),
                      exchange="CO", name="Growth Test Issuer", status="new")
    db.add(company)
    await db.flush()
    return company


async def _document(db, company, *, tier="T1_primary_filing") -> ExtractedDocument:  # noqa: ANN001
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url="https://www.issuer.example/ar-2025.pdf", provider="company_ir",
        source_type="annual_report", source_tier=tier, mime_type="application/pdf",
        extraction_method="native_pdf", status="extracted",
        retrieved_at=datetime.now(UTC), excerpts_json=[], company_id=company.id,
    )
    db.add(doc)
    await db.flush()
    return doc


def _fact(doc, value, period, *, scope="group", status="validated", active=True,  # noqa: ANN001
          currency="DKK") -> ExtractedFact:
    return ExtractedFact(
        id=uuid.uuid4(), extracted_document_id=doc.id, label="revenue",
        value_numeric=value, value_text=str(value), currency=currency, scale="million",
        period=period, extraction_method="native_pdf", confidence=0.9,
        validation_status=status, needs_human_review=False, is_active=active,
        scope_type=scope, scope_name=None, scope_key=scope,
    )


async def test_group_revenue_pair_is_a_verified_growth_observation(factory):
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add_all([_fact(doc, 32000, "FY2025"), _fact(doc, 28000, "FY2024"),
                    _fact(doc, 99999, "FY2025", scope="segment"),
                    _fact(doc, 1, "FY2023", status="rejected")])
        await db.flush()
        [obs] = await growth_from_held_facts(db, company.id)
        assert obs.metric == "revenue_pair"
        assert obs.growth_pct == pytest.approx(14.29, abs=0.01)
        assert (obs.period, obs.base_period) == ("FY2025", "FY2024")
        assert obs.source_tier == "T1_primary_filing" and obs.verified
        assert obs.source_url == "https://www.issuer.example/ar-2025.pdf"
        await db.rollback()


@pytest.mark.parametrize("variant", ["unknown_scope", "media", "currency", "gap", "inactive"])
async def test_no_trustworthy_pair_means_no_growth(factory, variant):
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company,
                              tier="T4_quality_media" if variant == "media" else
                              "T1_primary_filing")
        prior_period = "FY2022" if variant == "gap" else "FY2024"
        db.add_all([
            _fact(doc, 32000, "FY2025", scope=None if variant == "unknown_scope" else "group"),
            _fact(doc, 28000, prior_period,
                  scope=None if variant == "unknown_scope" else "group",
                  currency="EUR" if variant == "currency" else "DKK",
                  active=variant != "inactive"),
        ])
        await db.flush()
        assert await growth_from_held_facts(db, company.id) == []
        await db.rollback()


async def test_split_fiscal_years_pair_only_with_each_other(factory):
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add_all([_fact(doc, 22000, "2025/26"), _fact(doc, 20000, "2024/25"),
                    _fact(doc, 5, "FY2024")])
        await db.flush()
        [obs] = await growth_from_held_facts(db, company.id)
        assert (obs.period, obs.base_period) == ("2025/26", "2024/25")
        assert obs.growth_pct == pytest.approx(10.0)
        await db.rollback()


async def test_production_passes_a_string_company_id(factory):
    """The pipeline passes ``str(held_row.id)``; a swallowed type error would read as
    'no growth' forever."""
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add_all([_fact(doc, 110, "FY2025"), _fact(doc, 100, "FY2024")])
        await db.flush()
        [obs] = await growth_from_held_facts(db, str(company.id))
        assert obs.growth_pct == pytest.approx(10.0)
        await db.rollback()


async def test_an_annual_year_without_a_pair_does_not_hide_a_split_year_pair(factory):
    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        db.add_all([_fact(doc, 999, "FY2025"), _fact(doc, 22000, "2025/26"),
                    _fact(doc, 20000, "2024/25")])
        await db.flush()
        [obs] = await growth_from_held_facts(db, company.id)
        assert (obs.period, obs.base_period) == ("2025/26", "2024/25")
        await db.rollback()


async def test_a_document_owned_through_its_ingestion_attempt_counts(factory):
    """V3.19.14 — a reused document keeps its first extractor's company_id; this company
    owns it through its ingestion attempt (the platform's provenance link)."""
    from app.models.document_ingestion_attempt import DocumentIngestionAttempt

    async with factory() as db:
        company = await _company(db)
        doc = await _document(db, company)
        doc.company_id = None
        db.add(DocumentIngestionAttempt(
            id=uuid.uuid4(), company_id=company.id, canonical_url=doc.canonical_url,
            url_hash=uuid.uuid4().hex, source_type="company_ir_annual_report",
            source_tier="T1_primary_filing", doc_kind="annual_report",
            discovery_strategy="static_link", attempted_at=datetime.now(UTC),
            status="extracted", mime_type="application/pdf", http_status_class="2xx",
            extraction_method="native_pdf", page_count=10, content_hash=doc.content_hash,
            fetch_ms=1, extraction_ms=1, total_ms=2, pinned=True))
        db.add_all([_fact(doc, 110, "FY2025"), _fact(doc, 100, "FY2024")])
        await db.flush()
        [obs] = await growth_from_held_facts(db, company.id)
        assert obs.growth_pct == pytest.approx(10.0)
        # Another company never borrows it.
        other = await _company(db)
        assert await growth_from_held_facts(db, other.id) == []
        await db.rollback()
