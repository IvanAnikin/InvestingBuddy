"""ADR-092 addendum — a total line an OLDER pipeline read from a table is not served.

Measured live (CFR4, 2026-11-17, deployed ``e8d57c2``): Richemont's FY26 results PDF was
last read by pipeline version 23, before the statement-line selector (version 24). Its
active facts still held "Trade receivables and other current assets" (1 907) as current
assets and "Trade payables and other current liabilities" (3 086) as current liabilities.
The run never touched the document (it was outside the reuse window), so the statements
view kept serving them. The selector itself reads the same PDF correctly (28 934 / 9 341).

Rules under test:
* the statements view withholds a selector-governed field (current assets / liabilities,
  total assets / liabilities, non-current assets, cash, operating profit) a table read
  below version 24, with its own reason; an inline-XBRL fact, a version-24+ fact, any
  other field and a fact without a recorded reader are judged as before;
* the run re-reads such a document once (bounded), superseding the stale facts with the
  selector's reading; a re-read that fails leaves the facts withheld.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest

from app.services.pipeline.issuer_financials import (
    MAX_STALE_STATEMENT_REREADS,
    WITHHELD_READ_BEFORE_LINE_SELECTOR,
    build_financial_statements_state,
    reread_stale_statement_documents,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
    STATEMENT_LINE_SELECTOR_MIN_VERSION,
)

NOW = datetime(2026, 11, 17, tzinfo=UTC)


def _fact(field, value, *, version, method="native_pdf", period="2026", document="d1"):  # noqa: ANN001, ANN202
    return {
        "field": field, "value": str(value), "numeric_value": value,
        "unit": "currency_amount", "currency": "EUR", "scale": "million",
        "period": period, "scope": "group", "confidence": "high",
        "source_url": f"https://issuer.example/{document}.pdf", "page_number": 14,
        "excerpt_id": "p14:m0", "fact_id": f"{document}:{field}:{period}",
        "document_id": document, "document_title": "FY26 annual results",
        "document_date": "2026-05-22", "document_source_tier": "T1_primary_filing",
        "document_pipeline_version": version, "extraction_method": method,
    }


def _slot(state, field):  # noqa: ANN001, ANN202
    slot = (state.get("slots") or {}).get(f"{field}_primary_filing")
    return None if slot is None else slot.get("numeric_value")


def test_the_selector_version_is_the_one_that_shipped_it() -> None:
    assert STATEMENT_LINE_SELECTOR_MIN_VERSION == 24
    assert CURRENT_EXTRACTION_PIPELINE_VERSION >= STATEMENT_LINE_SELECTOR_MIN_VERSION


def test_a_total_read_before_the_selector_is_withheld() -> None:
    state = build_financial_statements_state(
        [_fact("total_current_assets", 1907, version=23),
         _fact("total_current_liabilities", 3086, version=None),
         _fact("revenue", 22420, version=23)],
        core_disclosures=None, core_filings=None, now=NOW)
    assert _slot(state, "total_current_assets") is None
    assert _slot(state, "total_current_liabilities") is None
    reasons = {(w["field"], w["reason"]) for w in state["withheld"]}
    assert ("total_current_assets", WITHHELD_READ_BEFORE_LINE_SELECTOR) in reasons
    assert ("total_current_liabilities", WITHHELD_READ_BEFORE_LINE_SELECTOR) in reasons
    assert state["withheld_counts"][WITHHELD_READ_BEFORE_LINE_SELECTOR] == 2
    # Revenue is not a line the selector changed: the old reading still stands.
    assert _slot(state, "revenue") == 22420


@pytest.mark.parametrize("fact", [
    _fact("total_current_assets", 28934, version=24),
    _fact("total_current_assets", 28934, version=CURRENT_EXTRACTION_PIPELINE_VERSION),
    _fact("total_current_assets", 28934, version=20, method="inline_xbrl"),
])
def test_a_selector_or_tag_reading_is_served(fact) -> None:  # noqa: ANN001
    state = build_financial_statements_state(
        [fact], core_disclosures=None, core_filings=None, now=NOW)
    assert _slot(state, "total_current_assets") == 28934


def test_a_fact_without_its_reader_is_judged_as_before() -> None:
    fact = _fact("total_current_assets", 28934, version=23)
    del fact["document_pipeline_version"]
    state = build_financial_statements_state(
        [fact], core_disclosures=None, core_filings=None, now=NOW)
    assert _slot(state, "total_current_assets") == 28934


async def test_no_company_or_ingestion_off_tries_nothing() -> None:
    class Cfg:
        primary_document_ingestion_enabled = False

    company = type("C", (), {"id": uuid.uuid4(), "name": "X", "ticker": "X"})()
    assert await reread_stale_statement_documents(object(), company, cfg=Cfg()) == []
    nobody = type("C", (), {"id": None})()
    assert await reread_stale_statement_documents(object(), nobody, cfg=Cfg()) == []


async def test_a_session_that_cannot_query_tries_nothing() -> None:
    class Cfg:
        primary_document_ingestion_enabled = True

    company = type("C", (), {"id": uuid.uuid4(), "name": "X", "ticker": "X"})()
    assert await reread_stale_statement_documents(object(), company, cfg=Cfg()) == []


# ── On PostgreSQL: the re-read supersedes the stale facts ─────────────────── #

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
needs_pg = pytest.mark.skipif(not POSTGRES_URL,
                              reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head")


@pytest.fixture
async def factory():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _stale_document(db, *, version=23):  # noqa: ANN001, ANN202
    from app.models.company import Company
    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    company = Company(id=uuid.uuid4(), ticker=f"R{uuid.uuid4().hex[:5]}".upper(),
                      exchange="SW", name="Stale Totals Test SA", status="new")
    db.add(company)
    await db.flush()
    doc = ExtractedDocument(
        id=uuid.uuid4(), content_hash=uuid.uuid4().hex * 2,
        canonical_url=f"https://issuer.example/{uuid.uuid4().hex[:8]}.pdf",
        provider="company_ir", source_type="annual_results",
        source_tier="T1_primary_filing", mime_type="application/pdf",
        title="FY26 annual results", doc_date=date(2026, 5, 22),
        extraction_method="native_pdf", status="extracted",
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC), excerpts_json=[],
        company_id=company.id, pipeline_version=version,
    )
    db.add(doc)
    await db.flush()
    for label, value in (("total_current_assets", 1907), ("total_current_liabilities", 3086)):
        db.add(ExtractedFact(
            id=uuid.uuid4(), extracted_document_id=doc.id, label=label,
            value_numeric=value, value_text=str(value), unit="currency_amount",
            currency="EUR", scale="million", period="2026", page_number=14,
            table_location="p14:m0", extraction_method="native_pdf", confidence=0.9,
            validation_status="validated", needs_human_review=True, is_active=True,
            scope_type="group", scope_key="group"))
    await db.flush()
    return company, doc


def _selector_artifact(doc):  # noqa: ANN001, ANN202
    """What the current reader makes of the document: the selector test's IFRS balance
    sheet (Richemont's layout; its unlabelled subtotals are 20 144 and 4 134)."""
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
    from app.services.sources.primary_document_extractor import PrimaryDocumentExtraction
    from tests.test_qa_closure_statement_line_selector import IFRS_BALANCE_SHEET, _table

    extraction = PrimaryDocumentExtraction(
        content_hash=doc.content_hash, mime_type="application/pdf",
        extraction_method="native_pdf", status="extracted",
        tables=[_table(IFRS_BALANCE_SHEET)])
    from app.core.config import Settings
    from app.services.sources.document_period import UNKNOWN_DOCUMENT_PERIOD
    from app.services.sources.extracted_fact_validator import (
        IssuerContext,
        validate_extracted_facts,
    )

    facts = validate_extracted_facts(
        extraction, issuer_context=IssuerContext(company_name="Stale Totals Test SA",
                                                 reporting_currency="EUR"),
        cfg=Settings(), document_period=UNKNOWN_DOCUMENT_PERIOD)
    return PrimaryDocumentArtifact(
        source_url=doc.canonical_url, document_type="annual_results", title=doc.title,
        retrieved_at=NOW, status="extracted", extraction=extraction, validated_facts=facts)


class _Cfg:
    primary_document_ingestion_enabled = True


@needs_pg
async def test_the_run_rereads_the_stale_document_and_serves_the_selector(factory) -> None:  # noqa: ANN001
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedFact
    from app.services.pipeline.issuer_financials import load_statement_facts

    async with factory() as db:
        company, doc = await _stale_document(db)
        artifact = _selector_artifact(doc)
        calls: list[str] = []

        async def extractor(url, **_kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(url)
            return artifact

        out = await reread_stale_statement_documents(db, company, cfg=_Cfg(),
                                                     extractor=extractor)
        assert out == [{"document_id": str(doc.id), "outcome": "reread"}]
        assert calls == [doc.canonical_url]
        active = (await db.execute(select(ExtractedFact).where(
            ExtractedFact.extracted_document_id == doc.id,
            ExtractedFact.is_active.is_(True),
            ExtractedFact.label == "total_current_assets"))).scalars().all()
        assert sorted(float(f.value_numeric) for f in active) == [19132.0, 20144.0]
        facts = await load_statement_facts(db, company.id)
        state = build_financial_statements_state(
            facts, core_disclosures=None, core_filings=None, now=NOW)
        assert _slot(state, "total_current_assets") == 20144
        assert _slot(state, "total_current_liabilities") == 4134
        # Up to date now: a second run tries nothing.
        assert await reread_stale_statement_documents(db, company, cfg=_Cfg(),
                                                       extractor=extractor) == []
        await db.rollback()


@needs_pg
async def test_a_failed_reread_keeps_the_stale_totals_withheld(factory) -> None:  # noqa: ANN001
    from app.services.pipeline.issuer_financials import load_statement_facts

    async with factory() as db:
        company, doc = await _stale_document(db)

        async def changed(url, **_kw):  # noqa: ANN001, ANN003, ANN202
            artifact = _selector_artifact(doc)
            artifact.extraction.content_hash = "0" * 64  # the document changed
            return artifact

        out = await reread_stale_statement_documents(db, company, cfg=_Cfg(),
                                                     extractor=changed)
        assert out == [{"document_id": str(doc.id), "outcome": "not_reread"}]
        facts = await load_statement_facts(db, company.id)
        state = build_financial_statements_state(
            facts, core_disclosures=None, core_filings=None, now=NOW)
        assert _slot(state, "total_current_assets") is None
        assert state["withheld_counts"][WITHHELD_READ_BEFORE_LINE_SELECTOR] == 2
        await db.rollback()


def test_the_reread_is_bounded() -> None:
    assert 1 <= MAX_STALE_STATEMENT_REREADS <= 3
