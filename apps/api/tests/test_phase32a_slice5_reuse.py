"""Phase 32A Slice 5 (3c-iii) — REUSE extraction results across report
regeneration (skip re-fetch / re-extract).

Exercises the two halves of the feature against a REAL in-memory SQLite async
database (``aiosqlite``) so the new ``excerpts_json`` column, the
``load_reusable_documents`` loader, and the connector deep-path fetch-skip are all
genuinely persisted and read back:

  * persist → ``load_reusable_documents`` rebuilds the bounded excerpts (with page
    / section) + the validated facts;
  * a FRESH persisted document → the connector reuses it and NEVER fetches (the
    injected deep extractor raises if called);
  * a STALE document (``retrieved_at`` older than the TTL, via an injected ``now``)
    → not reused, the fetch path is taken;
  * reuse is STRICTLY company-scoped (company B never sees company A's documents);
  * either gate flag OFF → empty lookup, no reuse, the fetch path is unchanged;
  * a reused artifact yields the SAME evidence + facts and re-persisting it creates
    NO duplicate rows (idempotent — content_hash / fact dedup);
  * ``excerpts_json`` round-trips the bounded excerpts with page + section.

Fully OFFLINE and deterministic: no network, no Azure, no LLM. Nothing here
touches auth, publishing, or app settings; gate flags are passed as explicit
``Settings`` instances per call. The same dialect-scoped ``JSONB -> JSON`` compiler
shim as the other Slice-5 DB tests lets ``create_all`` build the Postgres-flavoured
schema on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# --- Import every model module so Base.metadata is complete for create_all. ---
from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.extracted_document_service import (
    load_reusable_documents,
    persist_primary_document_artifacts,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    CompanyIrConnector,
    PrimaryDocumentArtifact,
)
from app.services.sources.document_text_extractor import (
    EVIDENCE_TYPE_BUSINESS,
    EVIDENCE_TYPE_GENERAL,
)
from app.services.sources.extracted_fact_validator import ValidatedFact
from app.services.sources.primary_document_extractor import (
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.redaction import canonicalize_source_url
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Deep-document evidence source types produced from an artifact's excerpts/facts.
_DEEP_TYPES = {
    "company_ir_annual_report_excerpt",
    "company_ir_business_description",
    "company_ir_risk_excerpt",
    "company_ir_financial_fact",
}

_URL = "https://www.richemont.com/reports/ar2024.pdf"


def _cfg(**over) -> Settings:
    base = dict(
        primary_document_ingestion_enabled=True,
        report_citation_persistence_enabled=True,
    )
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Real async SQLite fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Seed + artifact builders
# ---------------------------------------------------------------------------
async def _add_company(session, *, ticker: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="SW",
        name=name,
        country="Switzerland",
        sector="Consumer Discretionary",
        industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_run(session) -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(), workflow_name="company_analysis", status="completed"
    )
    session.add(run)
    await session.flush()
    return run


async def _count(session, model) -> int:
    return (
        await session.execute(select(func.count()).select_from(model))
    ).scalar_one()


def _excerpt(
    *,
    excerpt_id: str,
    text: str,
    page_number: int | None = None,
    section: str | None = None,
    heading: str | None = None,
    evidence_type: str = EVIDENCE_TYPE_GENERAL,
    confidence: float = 0.9,
) -> PrimaryDocumentExcerpt:
    return PrimaryDocumentExcerpt(
        excerpt_id=excerpt_id,
        text=text,
        page_number=page_number,
        section=section,
        heading=heading,
        table_location=None,
        extraction_method="native_pdf",
        confidence=confidence,
        char_count=len(text),
        evidence_type=evidence_type,
    )


def _fact(
    *,
    label: str,
    value_numeric: float | None,
    validation_status: str = "validated",
    period: str | None = "2024",
) -> ValidatedFact:
    return ValidatedFact(
        label=label,
        value_numeric=value_numeric,
        value_text=str(value_numeric) if value_numeric is not None else None,
        unit="currency_amount",
        currency="EUR",
        scale="million",
        period=period,
        page_number=12,
        table_location="p12:t0",
        extraction_method="native_pdf",
        confidence=0.91,
        validation_status=validation_status,
        needs_human_review=True,
    )


def _artifact(
    *,
    source_url: str = _URL,
    content_hash: str,
    excerpts: list[PrimaryDocumentExcerpt] | None = None,
    facts: list[ValidatedFact] | None = None,
    title: str = "Annual Report 2024",
) -> PrimaryDocumentArtifact:
    extraction = PrimaryDocumentExtraction(
        content_hash=content_hash,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status="extracted",
        page_count=180,
        excerpts=excerpts or [],
    )
    return PrimaryDocumentArtifact(
        source_url=source_url,
        document_type="annual_report",
        title=title,
        retrieved_at=_utcnow(),
        status="extracted",
        extraction=extraction,
        validated_facts=facts or [],
    )


# ---------------------------------------------------------------------------
# Connector deep-path helpers (offline: injected fetcher + extractor).
# ---------------------------------------------------------------------------
def _page_fetcher(links: list[SafeLink]):
    async def _fetch(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=list(links))

    return _fetch


def _links(url: str = _URL) -> list[SafeLink]:
    return [SafeLink(url=url, text="Annual Report 2024", is_document=True)]


def _returning_extractor(artifact: PrimaryDocumentArtifact, calls: list[str]):
    async def _extract(
        url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None
    ):
        calls.append(url)
        return artifact

    return _extract


def _raising_extractor(calls: list[str]):
    async def _extract(
        url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None
    ):
        calls.append(url)
        raise AssertionError("network fetch/extract must not run for a reused document")

    return _extract


def _connector(*, extractor, reuse=None) -> CompanyIrConnector:
    return CompanyIrConnector(
        verified_source=get_verified_issuer_source("CFR", "SW"),
        page_fetcher=_page_fetcher(_links()),
        primary_document_extractor=extractor,
        primary_document_reuse=reuse,
    )


async def _fetch_filings(conn: CompanyIrConnector):
    return await conn.fetch_filings(
        CompanyContext(ticker="CFR", exchange="SW"), QueryContext(max_items=20)
    )


def _deep_signature(items) -> list[tuple]:
    """Stable signature of the deep (excerpt / fact) evidence items only."""
    return sorted(
        (i.id, i.source_type, i.title, i.excerpt, i.url, tuple(i.provenance), i.data_quality)
        for i in items
        if i.source_type in _DEEP_TYPES
    )


# ===========================================================================
# 1. persist → load_reusable_documents rebuilds excerpts + validated facts.
# ===========================================================================
async def test_load_reusable_rebuilds_excerpts_and_facts(session):
    cfg = _cfg()
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    art = _artifact(
        content_hash="a" * 64,
        excerpts=[
            _excerpt(
                excerpt_id="X1",
                text="Business overview of the group.",
                page_number=3,
                section="Overview",
                evidence_type=EVIDENCE_TYPE_BUSINESS,
            ),
            _excerpt(
                excerpt_id="X2",
                text="Segment revenue disclosure.",
                page_number=12,
                section="Segment information",
            ),
        ],
        facts=[
            _fact(label="revenue", value_numeric=20616.0),
            _fact(label="net income", value_numeric=2357.0),
            _fact(label="prose only", value_numeric=None, validation_status="excerpt_only"),
        ],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )

    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    key = canonicalize_source_url(_URL)
    assert key in lookup
    reused = lookup[key]
    assert reused.content_hash == "a" * 64

    rebuilt = reused.artifact
    assert rebuilt.status == "extracted"
    assert rebuilt.source_url == _URL
    assert rebuilt.extraction is not None
    assert rebuilt.extraction.content_hash == "a" * 64

    # Excerpts rebuilt with page / section / evidence_type preserved.
    ex = rebuilt.extraction.excerpts
    assert [e.page_number for e in ex] == [3, 12]
    assert [e.section for e in ex] == ["Overview", "Segment information"]
    assert ex[0].evidence_type == EVIDENCE_TYPE_BUSINESS

    # Only the two VALIDATED facts are rebuilt (excerpt_only is never persisted).
    assert len(rebuilt.validated_facts) == 2
    rev = next(f for f in rebuilt.validated_facts if f.label == "revenue")
    assert rev.value_numeric == 20616.0
    assert rev.period == "2024"
    assert rev.table_location == "p12:t0"
    assert rev.validation_status == "validated"
    assert rev.needs_human_review is True


# ===========================================================================
# 2. Fresh persisted doc → connector reuses and does NOT fetch.
# ===========================================================================
async def test_fresh_doc_is_reused_and_not_fetched(session):
    cfg = _cfg()
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    art = _artifact(
        content_hash="b" * 64,
        excerpts=[_excerpt(excerpt_id="X1", text="Overview.", page_number=1)],
        facts=[_fact(label="revenue", value_numeric=20616.0)],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)

    calls: list[str] = []
    conn = _connector(extractor=_raising_extractor(calls), reuse=lookup)
    res = await _fetch_filings(conn)

    assert calls == []  # the extractor was NEVER invoked (no fetch)
    assert len(conn.collected_primary_document_artifacts) == 1
    types = {i.source_type for i in res.evidence_items}
    assert "company_ir_annual_report_excerpt" in types
    assert "company_ir_financial_fact" in types


# ===========================================================================
# 3. Stale doc (older than TTL) → NOT reused, fetch path taken.
# ===========================================================================
async def test_stale_doc_is_not_reused_and_fetch_path_taken(session):
    cfg = _cfg(primary_document_reuse_ttl_hours=24)
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    art = _artifact(
        content_hash="c" * 64,
        excerpts=[_excerpt(excerpt_id="X1", text="Overview.", page_number=1)],
        facts=[_fact(label="revenue", value_numeric=20616.0)],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )

    # Look up "from the future" so the doc's retrieved_at falls outside the TTL.
    future = _utcnow() + timedelta(hours=25)
    stale_lookup = await load_reusable_documents(
        session, company_id=company.id, cfg=cfg, now=future
    )
    assert stale_lookup == {}  # nothing fresh enough to reuse

    # A fresh (within-TTL) lookup would still find it — proving only the TTL matters.
    fresh_lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    assert canonicalize_source_url(_URL) in fresh_lookup

    # With the (empty) stale lookup the connector must fetch.
    calls: list[str] = []
    fresh_art = _artifact(
        content_hash="c" * 64,
        excerpts=[_excerpt(excerpt_id="X1", text="Overview.", page_number=1)],
        facts=[_fact(label="revenue", value_numeric=20616.0)],
    )
    conn = _connector(extractor=_returning_extractor(fresh_art, calls), reuse=stale_lookup)
    await _fetch_filings(conn)
    assert calls == [_URL]  # fetch path was taken


# ===========================================================================
# 4. Reuse is strictly company-scoped (B never sees A's documents).
# ===========================================================================
async def test_reuse_is_strictly_company_scoped(session):
    cfg = _cfg()
    company_a = await _add_company(session, ticker="CFR", name="Richemont")
    company_b = await _add_company(session, ticker="LVMH", name="LVMH")
    run = await _add_run(session)

    # SAME url for both companies but DIFFERENT content — proves isolation is by
    # company_id, not by URL.
    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(content_hash="a" * 64)],
        company_id=company_a.id,
        agent_run_id=run.id,
        cfg=cfg,
    )
    await persist_primary_document_artifacts(
        session,
        artifacts=[_artifact(content_hash="b" * 64)],
        company_id=company_b.id,
        agent_run_id=run.id,
        cfg=cfg,
    )

    key = canonicalize_source_url(_URL)
    lookup_a = await load_reusable_documents(session, company_id=company_a.id, cfg=cfg)
    lookup_b = await load_reusable_documents(session, company_id=company_b.id, cfg=cfg)

    assert set(lookup_a) == {key} and set(lookup_b) == {key}
    assert lookup_a[key].content_hash == "a" * 64  # A only sees A's document
    assert lookup_b[key].content_hash == "b" * 64  # B only sees B's document
    assert lookup_a[key].content_hash != lookup_b[key].content_hash


# ===========================================================================
# 5. Either gate flag OFF → empty lookup, no reuse, fetch path unchanged.
# ===========================================================================
async def test_flag_off_yields_empty_lookup_and_fetch_path(session):
    cfg_on = _cfg()
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    await persist_primary_document_artifacts(
        session,
        artifacts=[
            _artifact(
                content_hash="d" * 64,
                excerpts=[_excerpt(excerpt_id="X1", text="Overview.", page_number=1)],
                facts=[_fact(label="revenue", value_numeric=20616.0)],
            )
        ],
        company_id=company.id,
        agent_run_id=run.id,
        cfg=cfg_on,
    )

    # Both on → a document is found (sanity anchor).
    assert await load_reusable_documents(session, company_id=company.id, cfg=cfg_on)

    # Either flag off → empty dict (no reuse), regardless of persisted rows.
    off_ingestion = _cfg(primary_document_ingestion_enabled=False)
    off_citation = _cfg(report_citation_persistence_enabled=False)
    assert await load_reusable_documents(session, company_id=company.id, cfg=off_ingestion) == {}
    assert await load_reusable_documents(session, company_id=company.id, cfg=off_citation) == {}

    # With an empty lookup the connector fetches exactly as before.
    calls: list[str] = []
    art = _artifact(
        content_hash="d" * 64,
        excerpts=[_excerpt(excerpt_id="X1", text="Overview.", page_number=1)],
        facts=[_fact(label="revenue", value_numeric=20616.0)],
    )
    conn = _connector(extractor=_returning_extractor(art, calls), reuse={})
    await _fetch_filings(conn)
    assert calls == [_URL]


# ===========================================================================
# 6. Reused artifact yields the SAME evidence + facts; re-persist is idempotent.
# ===========================================================================
async def test_reuse_yields_same_evidence_and_idempotent_repersist(session):
    cfg = _cfg()
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    art = _artifact(
        content_hash="e" * 64,
        excerpts=[
            _excerpt(
                excerpt_id="X1",
                text="Business overview of the group.",
                page_number=3,
                section="Overview",
                evidence_type=EVIDENCE_TYPE_BUSINESS,
            ),
            _excerpt(
                excerpt_id="X2",
                text="Segment revenue disclosure.",
                page_number=12,
                section="Segment information",
            ),
        ],
        facts=[_fact(label="revenue", value_numeric=20616.0)],
    )

    # Run 1: fetch path (deep extractor returns the artifact).
    fetch_calls: list[str] = []
    conn_fetch = _connector(extractor=_returning_extractor(art, fetch_calls))
    res_fetch = await _fetch_filings(conn_fetch)
    assert fetch_calls == [_URL]
    evidence_fetch = _deep_signature(res_fetch.evidence_items)
    assert evidence_fetch  # produced deep evidence

    # Persist the fetched artifact.
    r1 = await persist_primary_document_artifacts(
        session,
        artifacts=conn_fetch.collected_primary_document_artifacts,
        company_id=company.id,
        agent_run_id=run.id,
        cfg=cfg,
    )
    assert r1.documents_created == 1 and r1.facts_created == 1

    # Run 2: reuse path (extractor raises if called).
    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reuse_calls: list[str] = []
    conn_reuse = _connector(extractor=_raising_extractor(reuse_calls), reuse=lookup)
    res_reuse = await _fetch_filings(conn_reuse)
    assert reuse_calls == []
    evidence_reuse = _deep_signature(res_reuse.evidence_items)

    # Byte-for-byte the same deep evidence from the reused artifact.
    assert evidence_reuse == evidence_fetch

    # Idempotent re-persist: the reused artifact adds NO new rows.
    before_docs = await _count(session, ExtractedDocument)
    before_facts = await _count(session, ExtractedFact)
    r2 = await persist_primary_document_artifacts(
        session,
        artifacts=conn_reuse.collected_primary_document_artifacts,
        company_id=company.id,
        agent_run_id=run.id,
        cfg=cfg,
    )
    assert r2.documents_created == 0 and r2.documents_reused == 1
    assert r2.facts_created == 0 and r2.facts_deduped == 1
    assert await _count(session, ExtractedDocument) == before_docs
    assert await _count(session, ExtractedFact) == before_facts


# ===========================================================================
# 7. excerpts_json round-trips the bounded excerpts with page + section.
# ===========================================================================
async def test_excerpts_json_round_trips_page_and_section(session):
    cfg = _cfg()
    company = await _add_company(session, ticker="CFR", name="Richemont")
    run = await _add_run(session)
    art = _artifact(
        content_hash="f" * 64,
        excerpts=[
            _excerpt(
                excerpt_id="X1",
                text="Segment revenue disclosure for the year.",
                page_number=12,
                section="Segment information",
                heading="Segment information",
            )
        ],
        facts=[],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )

    doc = (await session.execute(select(ExtractedDocument))).scalar_one()
    assert isinstance(doc.excerpts_json, list) and len(doc.excerpts_json) == 1
    stored = doc.excerpts_json[0]
    assert stored["page_number"] == 12
    assert stored["section"] == "Segment information"
    assert stored["heading"] == "Segment information"
    assert stored["extraction_method"] == "native_pdf"
    assert stored["text"] == "Segment revenue disclosure for the year."
    assert stored["confidence"] == 0.9

    # And the round-trip through the loader preserves them.
    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    rebuilt = lookup[canonicalize_source_url(_URL)].artifact
    assert rebuilt.extraction.excerpts[0].page_number == 12
    assert rebuilt.extraction.excerpts[0].section == "Segment information"
