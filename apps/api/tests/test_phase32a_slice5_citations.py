"""Phase 32A Slice 5 (3c-ii) — deep primary-document citation integration.

Covers, OFFLINE (no network / Azure), the citation page/section/table surfacing
and honest final-report reconciliation added on top of the Slice-3 model:

  * a DEEP primary-document evidence item carries its page / section / table
    location + extraction method + confidence into the citation representation;
  * an ``extraction_failed`` / ``metadata_only`` artifact yields NO verification
    citation (metadata-only stays reference-only);
  * an OCR-method item discloses its OCR provenance;
  * repeated report generation does not duplicate sources / evidence / citations,
    and deep excerpts + facts from ONE document collapse to ONE canonical Source
    (raw-bytes document hash) — never inflated;
  * no cross-company linkage;
  * the appendix reports extracted / metadata-only / extraction-failed as DISTINCT
    honest counts (never summed);
  * the raw-bytes ``content_hash`` is used for deep documents;
  * with the master flag OFF the appendix / citation output is byte-identical to
    the Slice-3-only behaviour.

Helper-level tests use plain in-memory objects; the persistence + end-to-end
appendix tests use a REAL in-memory SQLite async DB (mirroring the Slice-3 suite),
with the same ``JSONB -> JSON`` compiler shim so the Postgres-flavoured schema
builds on SQLite. The LLM council is patched to a deterministic result — no live
call. Invariants (publication_ready False, human_review_required True, draft) are
asserted preserved.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.report import Report
from app.models.source import Citation, Source
from app.services import final_report_generator
from app.services.final_report_generator import (
    FinalReportGeneratorService,
    _evidence_content_hash,
    _evidence_reconciliation_counts,
    _is_deep_primary_document,
    _persist_council_evidence_citations,
    _primary_document_citation_rows,
    _primary_document_provenance,
    _primary_document_state_counts,
    _source_content_hash,
)
from app.services.llm.schemas import (
    AgentKeyPoint,
    AgentRiskGap,
    CouncilAgentOutput,
    CouncilResult,
    PersistableEvidence,
)
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.primary_document_extractor import PrimaryDocumentExtraction
from app.services.sources.redaction import canonicalize_source_url

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_DOC_HASH_A = "a" * 64
_DOC_HASH_B = "b" * 64
_IR_URL = "https://investor.example.com/annual-report-2024.pdf"


# ---------------------------------------------------------------------------
# Deterministic evidence + council builders (no network)
# ---------------------------------------------------------------------------
def _deep_excerpt_pe(
    alias: str,
    *,
    doc_hash: str = _DOC_HASH_A,
    page: int | None = 12,
    section: str | None = "Business Overview",
    method: str = "native_pdf",
    confidence: str = "0.85",
    source_type: str = "company_ir_business_description",
) -> PersistableEvidence:
    provenance = [
        "Extracted from issuer annual-report document (deep, bounded text)",
        f"page={page}" if page else "page=unknown",
    ]
    if section:
        provenance.append(f"section={section}")
    provenance.append(f"method={method}")
    provenance.append(f"confidence={confidence}")
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type=source_type,
        provider_transport="Company IR / newsroom (issuer-published)",
        title="Annual Report 2024 — excerpt",
        url=_IR_URL,
        excerpt="The group operates across three reportable segments.",
        data_quality="B",
        fields_supported=["business_description"],
        provenance=provenance,
        document_content_hash=doc_hash,
    )


def _deep_fact_pe(
    alias: str,
    *,
    doc_hash: str = _DOC_HASH_A,
    page: int = 42,
    table: str = "p42:t3",
    method: str = "native_pdf",
    confidence: str = "0.91",
) -> PersistableEvidence:
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        provider_transport="Company IR / newsroom (issuer-published)",
        title="Annual Report 2024: Revenue",
        url=_IR_URL,
        excerpt="Revenue = 20819 (millions EUR) [FY2024]",
        data_quality="B",
        fields_supported=["Revenue"],
        provenance=[
            "Validated from issuer annual-report table (deep, stricter grid validation)",
            f"page={page}",
            f"table={table}",
            f"method={method}",
            f"confidence={confidence}",
            "validation_status=validated",
            "needs_human_review=true",
        ],
        primary_fact={
            "field": "Revenue",
            "value": "20819",
            "numeric_value": 20819.0,
            "unit": None,
            "currency": "EUR",
            "scale": "millions",
            "period": "FY2024",
            "page_number": page,
            "excerpt_id": table,
            "confidence": "high",
        },
        document_content_hash=doc_hash,
    )


def _metadata_only_pe(alias: str) -> PersistableEvidence:
    """A located primary-source REFERENCE — never deep-ingested (no doc hash)."""
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type="company_ir_annual_report_index",
        provider_transport="Company IR / newsroom (issuer-published)",
        title="Investor relations — annual reports index",
        url="https://investor.example.com/reports/",
        excerpt=None,
        data_quality="metadata_only",
        fields_supported=[],
        provenance=["Issuer annual-reports index (company-owned)."],
    )


def _shallow_excerpt_pe(alias: str) -> PersistableEvidence:
    """A Phase 29B.2 (shallow) excerpt — page in provenance but NO doc hash."""
    return PersistableEvidence(
        uid=uuid.uuid4().hex,
        alias=alias,
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type="company_ir_annual_report_excerpt",
        provider_transport="Company IR / newsroom (issuer-published)",
        title="Annual Report 2024 — excerpt",
        url=_IR_URL,
        excerpt="Shallow bounded excerpt.",
        data_quality="C",
        fields_supported=["general"],
        provenance=[
            "Extracted from issuer annual-report document (bounded text)",
            "page=7",
        ],
    )


def _agent_citing(agent_name: str, *, points: list[str], gaps: list[str] | None = None):
    return CouncilAgentOutput(
        agent_name=agent_name,
        status="completed",
        summary="Deep primary-document review.",
        key_points=[
            AgentKeyPoint(claim=f"claim about {cid}", citation_ids=[cid])
            for cid in points
        ],
        risks_or_gaps=[
            AgentRiskGap(item=f"gap about {cid}", citation_ids=[cid])
            for cid in (gaps or [])
        ],
    )


def _council(
    *,
    persistable: list[PersistableEvidence],
    agents: list[CouncilAgentOutput],
    artifacts: list[PrimaryDocumentArtifact] | None = None,
) -> CouncilResult:
    cr = CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-model",
        evidence_item_count=len(persistable),
        agents=agents,
        persistable_evidence=persistable,
    )
    if artifacts:
        cr.primary_document_artifacts = artifacts
    cr.recount()
    return cr


def _artifact(*, status: str, content_hash: str, url: str = _IR_URL) -> PrimaryDocumentArtifact:
    extraction = None
    if content_hash:
        extraction = PrimaryDocumentExtraction(
            content_hash=content_hash,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status=status,
            page_count=180,
        )
    return PrimaryDocumentArtifact(
        source_url=url,
        document_type="annual_report",
        title="Annual Report 2024",
        retrieved_at=_utcnow(),
        status=status,
        extraction=extraction,
    )


# ===========================================================================
# C3 — provenance carried into the citation representation (helper-level)
# ===========================================================================
def test_deep_excerpt_provenance_carries_page_section_method_confidence() -> None:
    item = _deep_excerpt_pe("E1", page=12, section="Business Overview", confidence="0.85")
    prov = _primary_document_provenance(item)
    assert prov is not None
    assert prov["page_number"] == 12
    assert prov["section"] == "Business Overview"
    assert prov["table_location"] is None
    assert prov["extraction_method"] == "native_pdf"
    assert prov["confidence"] == pytest.approx(0.85)
    assert prov["document_content_hash"] == _DOC_HASH_A
    assert "ocr_disclosure" not in prov


def test_deep_fact_provenance_carries_page_and_table_location() -> None:
    item = _deep_fact_pe("E1", page=42, table="p42:t3")
    prov = _primary_document_provenance(item)
    assert prov is not None
    assert prov["page_number"] == 42
    assert prov["table_location"] == "p42:t3"
    assert prov["extraction_method"] == "native_pdf"
    assert prov["confidence"] == pytest.approx(0.91)


def test_non_deep_items_have_no_primary_document_provenance() -> None:
    # A shallow excerpt (has page in provenance but NO document hash) and a
    # metadata-only reference both return None: only DEEP items surface here.
    assert _primary_document_provenance(_shallow_excerpt_pe("E1")) is None
    assert _primary_document_provenance(_metadata_only_pe("E1")) is None
    assert _is_deep_primary_document(_shallow_excerpt_pe("E1")) is False
    assert _is_deep_primary_document(_deep_excerpt_pe("E1")) is True


def test_ocr_method_evidence_discloses_ocr_provenance() -> None:
    item = _deep_excerpt_pe("E1", method="ocr")
    prov = _primary_document_provenance(item)
    assert prov is not None
    assert prov["extraction_method"] == "ocr"
    assert "ocr_disclosure" in prov
    assert "ocr" in prov["ocr_disclosure"].lower()


def test_primary_document_citation_rows_carry_provenance_and_exclude_references() -> None:
    persistable = [
        _deep_excerpt_pe("E1"),
        _deep_fact_pe("E2"),
        _metadata_only_pe("E3"),
    ]
    agents = [
        _agent_citing("fundamentals", points=["E1", "E2"], gaps=["E3"]),
    ]
    cr = _council(persistable=persistable, agents=agents)
    rows = _primary_document_citation_rows(cr)
    # Only the two DEEP items produce citation rows; the metadata-only reference
    # (E3) is NEVER surfaced as claim verification.
    assert len(rows) == 2
    kinds = {r["source_type"] for r in rows}
    assert "company_ir_business_description" in kinds
    assert "company_ir_financial_fact" in kinds
    fact_row = next(r for r in rows if r["source_type"] == "company_ir_financial_fact")
    assert fact_row["page_number"] == 42
    assert fact_row["table_location"] == "p42:t3"
    assert all(r["document_content_hash"] == _DOC_HASH_A for r in rows)
    assert all("claim_text" in r for r in rows)


def test_primary_document_citation_rows_empty_when_no_deep_evidence() -> None:
    # An extraction_failed / metadata-only-only run has no deep items ⇒ no rows.
    cr = _council(
        persistable=[_metadata_only_pe("E1")],
        agents=[_agent_citing("source_quality_critic", points=["E1"])],
    )
    assert _primary_document_citation_rows(cr) == []


# ===========================================================================
# C4 — raw-bytes content hash + distinct honest states (helper-level)
# ===========================================================================
def test_source_content_hash_uses_raw_bytes_for_deep_and_synthesized_for_shallow() -> None:
    deep = _deep_excerpt_pe("E1", doc_hash=_DOC_HASH_A)
    shallow = _shallow_excerpt_pe("E2")
    canonical = canonicalize_source_url(deep.url)
    # Deep item → its raw-bytes document hash (NOT the synthesized hash).
    assert _source_content_hash(deep, canonical) == _DOC_HASH_A
    assert _source_content_hash(deep, canonical) != _evidence_content_hash(deep, canonical)
    # Shallow item → the existing synthesized url+tier+excerpt hash (unchanged).
    assert _source_content_hash(
        shallow, canonicalize_source_url(shallow.url)
    ) == _evidence_content_hash(shallow, canonicalize_source_url(shallow.url))


def test_deep_excerpt_and_fact_from_same_document_share_one_source_hash() -> None:
    exc = _deep_excerpt_pe("E1", doc_hash=_DOC_HASH_A)
    fact = _deep_fact_pe("E2", doc_hash=_DOC_HASH_A)
    other = _deep_excerpt_pe("E3", doc_hash=_DOC_HASH_B)
    assert _source_content_hash(exc, canonicalize_source_url(exc.url)) == _source_content_hash(
        fact, canonicalize_source_url(fact.url)
    )
    assert _source_content_hash(exc, canonicalize_source_url(exc.url)) != _source_content_hash(
        other, canonicalize_source_url(other.url)
    )


def test_primary_document_state_counts_are_distinct_and_never_summed() -> None:
    cr = _council(
        persistable=[],
        agents=[],
        artifacts=[
            _artifact(status="extracted", content_hash=_DOC_HASH_A),
            _artifact(status="extracted", content_hash=_DOC_HASH_B),
            _artifact(status="metadata_only", content_hash="c" * 64),
            _artifact(status="extraction_failed", content_hash="d" * 64),
        ],
    )
    counts = _primary_document_state_counts(cr)
    assert counts["primary_document_extracted_count"] == 2
    assert counts["primary_document_metadata_only_count"] == 1
    assert counts["primary_document_extraction_failed_count"] == 1
    # The three states are reported side-by-side — never collapsed into one total.
    assert set(counts) == {
        "primary_document_extracted_count",
        "primary_document_metadata_only_count",
        "primary_document_extraction_failed_count",
    }


def test_reconciliation_counts_one_source_per_document_no_inflation() -> None:
    # 2 deep excerpts + 1 deep fact, all from the SAME document, each cited once.
    persistable = [
        _deep_excerpt_pe("E1", doc_hash=_DOC_HASH_A),
        _deep_excerpt_pe("E2", doc_hash=_DOC_HASH_A, page=13, section="Segments"),
        _deep_fact_pe("E3", doc_hash=_DOC_HASH_A),
    ]
    cr = _council(
        persistable=persistable,
        agents=[_agent_citing("fundamentals", points=["E1", "E2", "E3"])],
    )
    counts = _evidence_reconciliation_counts(cr, [], [], {})
    assert counts["council_claim_citation_count"] == 3
    assert counts["db_persisted_citation_count"] == 3
    # All three deep items collapse to ONE canonical Source (raw-bytes identity).
    assert counts["db_persisted_source_count"] == 1
    assert counts["extracted_evidence_count"] == 2
    assert counts["structured_financial_fact_count"] == 1


# ===========================================================================
# Real async SQLite fixtures (mirrors the Slice-3 suite)
# ===========================================================================
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


@pytest.fixture
def flags_on(monkeypatch):
    """Both the citation-persistence + master ingestion flags ON for one test."""
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    return True


async def _add_company(session, *, ticker: str, exchange: str, name: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        name=name,
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_completed_run(session) -> AgentRun:
    run = AgentRun(
        id=uuid.uuid4(),
        workflow_name="company_analysis",
        workflow_version="1.0.0",
        status="completed",
        started_at=_utcnow(),
        trigger_type="manual",
    )
    session.add(run)
    await session.flush()
    return run


async def _add_draft_report(
    session, *, company_id: uuid.UUID, agent_run_id: uuid.UUID, title: str = "Draft"
) -> Report:
    report = Report(
        id=uuid.uuid4(),
        title=title,
        slug=f"draft-{uuid.uuid4().hex[:12]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        content_markdown="# Analysis Council Draft (no envelope)",
        created_by_agent_run_id=agent_run_id,
        company_id=company_id,
        human_review_required=True,
        created_at=_utcnow(),
    )
    session.add(report)
    await session.flush()
    return report


def _patch_council(monkeypatch, by_ticker: dict[str, CouncilResult]) -> None:
    async def _fake(*args, **kwargs):
        return by_ticker.get(kwargs.get("ticker")) or CouncilResult.disabled()

    monkeypatch.setattr(final_report_generator, "maybe_run_council", _fake)


def _parse_report_content(report: Report) -> dict:
    md = report.content_markdown or ""
    blocks = re.findall(r"```json\s*(.*?)\s*```", md, re.DOTALL)
    assert blocks, "saved final report has no JSON block"
    return json.loads(blocks[-1])


async def _load_report(session, report_id: uuid.UUID) -> Report:
    return (
        await session.execute(select(Report).where(Report.id == report_id))
    ).scalar_one()


async def _council_citations(session, report_id: uuid.UUID) -> list[Citation]:
    rows = (
        await session.execute(
            select(Citation).where(
                Citation.report_id == report_id,
                Citation.field_path.like("council:%"),
            )
        )
    ).scalars().all()
    return list(rows)


def _deep_council() -> CouncilResult:
    persistable = [
        _deep_excerpt_pe("E1", doc_hash=_DOC_HASH_A),
        _deep_fact_pe("E2", doc_hash=_DOC_HASH_A),
        _metadata_only_pe("E3"),
    ]
    agents = [
        _agent_citing("fundamentals", points=["E1", "E2"], gaps=["E3"]),
    ]
    return _council(
        persistable=persistable,
        agents=agents,
        artifacts=[
            _artifact(status="extracted", content_hash=_DOC_HASH_A),
            _artifact(status="metadata_only", content_hash=_DOC_HASH_B),
            _artifact(status="extraction_failed", content_hash="d" * 64),
        ],
    )


# ===========================================================================
# Persistence — idempotency, references, cross-company (real DB)
# ===========================================================================
async def test_deep_document_citations_persist_one_source_and_are_idempotent(
    session, flags_on
) -> None:
    company = await _add_company(session, ticker="AAA", exchange="NYSE", name="Alpha")
    run = await _add_completed_run(session)
    report = await _add_draft_report(session, company_id=company.id, agent_run_id=run.id)
    await session.commit()

    cr = _council(
        persistable=[
            _deep_excerpt_pe("E1", doc_hash=_DOC_HASH_A),
            _deep_excerpt_pe("E2", doc_hash=_DOC_HASH_A, page=13, section="Segments"),
            _deep_fact_pe("E3", doc_hash=_DOC_HASH_A),
        ],
        agents=[_agent_citing("fundamentals", points=["E1", "E2", "E3"])],
    )

    src1, cit1 = await _persist_council_evidence_citations(session, report.id, run.id, cr)
    await session.commit()
    # 3 claim links → 3 citations; all 3 deep items → ONE Source (raw-bytes hash).
    assert cit1 == 3
    assert src1 == 1
    assert len(await _council_citations(session, report.id)) == 3
    source_hashes = (
        await session.execute(select(Source.content_hash))
    ).scalars().all()
    assert _DOC_HASH_A in source_hashes

    # Idempotent re-generation: delete-before-insert + hash dedup ⇒ no growth.
    await _persist_council_evidence_citations(session, report.id, run.id, cr)
    await session.commit()
    assert len(await _council_citations(session, report.id)) == 3
    assert (await session.execute(select(func.count()).select_from(Source))).scalar_one() == 1


async def test_metadata_only_citation_is_reference_only_never_verification(
    session, flags_on
) -> None:
    company = await _add_company(session, ticker="BBB", exchange="NYSE", name="Beta")
    run = await _add_completed_run(session)
    report = await _add_draft_report(session, company_id=company.id, agent_run_id=run.id)
    await session.commit()

    cr = _council(
        persistable=[_deep_excerpt_pe("E1"), _metadata_only_pe("E2")],
        agents=[_agent_citing("source_quality_critic", points=["E1"], gaps=["E2"])],
    )
    await _persist_council_evidence_citations(session, report.id, run.id, cr)
    await session.commit()

    cits = await _council_citations(session, report.id)
    assert len(cits) == 2
    # The metadata-only citation asserts NO fact-like quote (reference only) and
    # keeps the metadata_only sentinel; the deep excerpt carries a bounded quote.
    meta = [c for c in cits if c.data_quality == "metadata_only"]
    assert len(meta) == 1
    assert meta[0].source_quote is None
    # It is NEVER surfaced as a primary-document verification citation.
    rows = _primary_document_citation_rows(cr)
    assert all(r["source_type"] != "company_ir_annual_report_index" for r in rows)
    assert len(rows) == 1


async def test_no_cross_company_linkage(session, flags_on) -> None:
    a = await _add_company(session, ticker="AAA", exchange="NYSE", name="Alpha")
    b = await _add_company(session, ticker="BBB", exchange="NYSE", name="Beta")
    run_a = await _add_completed_run(session)
    run_b = await _add_completed_run(session)
    report_a = await _add_draft_report(session, company_id=a.id, agent_run_id=run_a.id)
    report_b = await _add_draft_report(session, company_id=b.id, agent_run_id=run_b.id)
    await session.commit()

    cr = _council(
        persistable=[_deep_excerpt_pe("E1"), _deep_fact_pe("E2")],
        agents=[_agent_citing("fundamentals", points=["E1", "E2"])],
    )
    await _persist_council_evidence_citations(session, report_a.id, run_a.id, cr)
    await session.commit()

    # Every citation is scoped to report A / run A; report B has none.
    a_cits = await _council_citations(session, report_a.id)
    assert len(a_cits) == 2
    assert all(c.agent_run_id == run_a.id for c in a_cits)
    assert await _council_citations(session, report_b.id) == []


# ===========================================================================
# End-to-end appendix surfacing + flag-off byte-identity (real DB)
# ===========================================================================
async def test_appendix_surfaces_distinct_states_and_provenance_end_to_end(
    session, flags_on, monkeypatch
) -> None:
    _patch_council(monkeypatch, {"AAA": _deep_council()})
    company = await _add_company(session, ticker="AAA", exchange="NYSE", name="Alpha")
    run = await _add_completed_run(session)
    await _add_draft_report(session, company_id=company.id, agent_run_id=run.id)
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, company.id)
    final = await _load_report(session, resp.report_id)
    ap = _parse_report_content(final)["source_citation_appendix"]

    # Distinct honest states (never summed).
    assert ap["primary_document_extracted_count"] == 1
    assert ap["primary_document_metadata_only_count"] == 1
    assert ap["primary_document_extraction_failed_count"] == 1

    # Primary-document citation representation carries page/section/table location.
    pdc = ap["primary_document_citations"]
    assert pdc["total"] == 2  # deep excerpt + deep fact (metadata-only excluded)
    fact_row = next(
        r for r in pdc["value"] if r["source_type"] == "company_ir_financial_fact"
    )
    assert fact_row["page_number"] == 42
    assert fact_row["table_location"] == "p42:t3"
    exc_row = next(
        r for r in pdc["value"] if r["source_type"] == "company_ir_business_description"
    )
    assert exc_row["section"] == "Business Overview"
    assert "primary_document_note" in ap

    # Raw-bytes document hash is the canonical Source identity for deep docs.
    source_hashes = (await session.execute(select(Source.content_hash))).scalars().all()
    assert _DOC_HASH_A in source_hashes

    # Invariants preserved.
    assert resp.publication_ready is False
    assert resp.human_review_required is True
    assert final.status == "draft"


async def test_flag_off_appendix_has_no_primary_document_keys(
    session, monkeypatch
) -> None:
    # Persistence flag ON (so Slice-3 reconciliation runs) but the MASTER ingestion
    # flag OFF ⇒ none of the Slice-5 deep keys appear (byte-identical to Slice 3).
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", False, raising=False
    )
    _patch_council(monkeypatch, {"AAA": _deep_council()})
    company = await _add_company(session, ticker="AAA", exchange="NYSE", name="Alpha")
    run = await _add_completed_run(session)
    await _add_draft_report(session, company_id=company.id, agent_run_id=run.id)
    await session.commit()

    resp = await FinalReportGeneratorService().generate_from_company(session, company.id)
    final = await _load_report(session, resp.report_id)
    ap = _parse_report_content(final)["source_citation_appendix"]

    for key in (
        "primary_document_citations",
        "primary_document_note",
        "primary_document_extracted_count",
        "primary_document_metadata_only_count",
        "primary_document_extraction_failed_count",
    ):
        assert key not in ap
