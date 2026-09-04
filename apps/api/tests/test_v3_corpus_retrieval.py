"""The corpus retrieval service — V3.1 Slice 1.6.

WHAT THESE TESTS PIN
====================
The V3.1 phase demonstration's second and third clauses: *a query months later
returns the right page with citable lineage*, and *period and scope filters
actually constrain results*.

  * a result is citable WITHOUT a second lookup — document, version, derivation,
    chunk, page, section, URL, tier, period, scope, offsets;
  * a stable evidence id resolves back to the exact span, its page, and the GRID
    when it came from a table;
  * the citation label names the scope and the period, because "Revenue 32,549"
    and "Group revenue, FY2025: 32,549" are different claims;
  * **no backend query syntax is reachable** — the service takes typed primitives
    and there is nowhere to put an expression;
  * the two retrieval refusals hold at the service, not only at the query object;
  * an unknown evidence id resolves to None rather than to an approximation.

The service is exercised against the real in-memory backend and a real SQLite
database, end to end from ingestion.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_artifact as _research_artifact  # noqa: F401
from app.models import research_chunk as _research_chunk  # noqa: F401
from app.models import research_derivation as _research_derivation  # noqa: F401
from app.models import research_document as _research_document  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.indexing import index_version, persist_chunks
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.corpus.retrieval import (
    EVIDENCE_ID_PREFIX,
    EvidenceReference,
    chunk_id_from_evidence_id,
    evidence_id_for,
    resolve_evidence,
    search_corpus,
)
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.types import (
    SearchMode,
    UnscopedRetrievalError,
    VectorOnlyRetrievalError,
)
from app.services.sources.primary_document_extractor import (
    ExtractedBlock,
    ExtractedTable,
    PrimaryDocumentExtraction,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
CFG = Settings(v3_corpus_enabled=True)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _corpus(session, company_id=None, *, period_key="2025"):  # noqa: ANN001, ANN202
    """Seed one indexed document and return (company_id, version, backend)."""
    company_id = company_id or uuid.uuid4()
    document = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        document_key=f"annual_report:{period_key}",
        document_type="annual_report",
        first_seen_at=T0,
        last_seen_at=T0,
    )
    session.add(document)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        canonical_url="https://pandoragroup.com/annual-report-2025.pdf",
        title="Annual Report 2025",
        transport="company_ir",
        source_tier=T1_PRIMARY_FILING,
        access_class="public_issuer",
        retrieved_at=T0,
        extraction_status="extracted",
        is_current=True,
        period_key=period_key,
        period_type="annual",
        language="en",
        published_at=date(2026, 2, 4),
    )
    session.add(version)
    await session.flush()

    table = ExtractedTable(
        table_location="p14:m0",
        table_index=0,
        page_number=14,
        rows=[["DKK million", "2025", "2024"], ["Revenue", "32,549", "31,680"]],
        row_count=2,
        col_count=3,
        reconstructed=True,
        column_periods=["2025", "2024"],
        scope="Group",
    )
    parsed = build_parsed_document(
        PrimaryDocumentExtraction(
            content_hash="a" * 64,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status="extracted",
            page_count=169,
            language="en",
            blocks=[
                ExtractedBlock(
                    page_number=12,
                    section="Group results",
                    text="Group revenue was DKK 32,549 million, up six per cent.",
                ),
                ExtractedBlock(
                    page_number=48,
                    section="Segment information",
                    text="Specialist Watchmakers revenue was EUR 107 million.",
                ),
            ],
            tables=[table],
        )
    )
    derivation = await persist_parsed_document(
        session, version_id=version.id, parsed=parsed, cfg=CFG
    )
    assert derivation is not None and parsed is not None
    await persist_chunks(
        session, version=version, derivation=derivation, parsed=parsed, cfg=CFG
    )
    backend = InMemorySearchBackend()
    await index_version(
        session, research_document_version_id=version.id, backend=backend, cfg=CFG
    )
    return company_id, version, backend


# --------------------------------------------------------------------------- #
# The service contract
# --------------------------------------------------------------------------- #


class TestServiceContract:
    def test_no_backend_query_syntax_is_reachable(self) -> None:
        # The rule that matters once an agent is the caller: tool arguments are
        # validated, not interpolated. There is deliberately nowhere to put a
        # query expression, a filter dict or a backend option bag.
        signature = inspect.signature(search_corpus)
        for name, parameter in signature.parameters.items():
            annotation = str(parameter.annotation)
            assert "dict" not in annotation.lower(), name
            assert "CorpusQuery" not in annotation, name
            assert "kwargs" not in name
        assert "raw" not in signature.parameters
        assert "options" not in signature.parameters

    def test_every_filter_dimension_is_a_named_argument(self) -> None:
        names = set(inspect.signature(search_corpus).parameters)
        for expected in (
            "company_ids",
            "document_types",
            "source_tiers",
            "period_keys",
            "period_types",
            "scope_types",
            "scope_keys",
            "languages",
            "access_classes",
            "published_from",
            "published_to",
            "top_k",
        ):
            assert expected in names, expected

    async def test_the_corpus_flag_off_returns_no_results_rather_than_failing(
        self, session
    ) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        assert (
            await search_corpus(
                session,
                backend=backend,
                cfg=Settings(v3_corpus_enabled=False),
                query="revenue",
                company_ids=[company_id],
            )
            == []
        )

    async def test_the_refusals_hold_at_the_service(self, session) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        with pytest.raises(VectorOnlyRetrievalError):
            await search_corpus(
                session,
                backend=backend,
                cfg=CFG,
                query="revenue",
                company_ids=[company_id],
                mode=SearchMode.SEMANTIC,
            )
        with pytest.raises(UnscopedRetrievalError):
            await search_corpus(session, backend=backend, cfg=CFG, query="revenue")


# --------------------------------------------------------------------------- #
# Citable results
# --------------------------------------------------------------------------- #


class TestCitableResults:
    async def test_a_result_carries_everything_a_citation_needs(self, session) -> None:  # noqa: ANN001
        company_id, version, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="group revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        assert results
        ref = results[0].reference
        assert isinstance(ref, EvidenceReference)
        assert ref.research_document_id == version.research_document_id
        assert ref.research_document_version_id == version.id
        assert ref.derivation_id is not None
        assert ref.company_id == company_id
        assert ref.canonical_url == "https://pandoragroup.com/annual-report-2025.pdf"
        assert ref.title == "Annual Report 2025"
        assert ref.source_tier == T1_PRIMARY_FILING
        assert ref.document_type == "annual_report"
        assert ref.period_key == "2025"
        assert ref.period_type == "annual"
        assert ref.scope_type is not None
        assert ref.page_start is not None
        assert ref.section_path is not None
        assert ref.char_start is not None and ref.char_end is not None
        assert ref.published_at == date(2026, 2, 4)

    async def test_the_scores_are_kept_per_leg(self, session) -> None:  # noqa: ANN001
        # "matched the metric name exactly" and "topically similar" are different
        # claims and a reader deciding whether to trust a citation wants to know
        # which one they are looking at.
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="group revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        assert results[0].lexical_score is not None
        assert results[0].semantic_score is None
        assert results[0].matched_terms

    async def test_the_citation_label_names_the_scope_and_the_period(
        self, session
    ) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="group revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        label = results[0].reference.citation_label()
        assert "Annual Report 2025" in label
        assert "p. 12" in label
        assert "2025" in label

    async def test_a_table_result_cites_its_table_not_a_page_it_has_no_claim_to(
        self, session
    ) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="32,549 31,680",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        table_results = [r for r in results if r.reference.is_table]
        assert table_results
        assert "table p14:m0" in table_results[0].reference.citation_label()


# --------------------------------------------------------------------------- #
# The filters, at the service level
# --------------------------------------------------------------------------- #


class TestFiltersThroughTheService:
    async def test_a_period_filter_constrains(self, session) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        await _corpus(session, company_id, period_key="2024")
        # Re-index both versions into the one backend.
        versions = (
            (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        )
        for version in versions:
            await index_version(
                session,
                research_document_version_id=version.id,
                backend=backend,
                cfg=CFG,
            )
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="group revenue",
            company_ids=[company_id],
            period_keys=["2025"],
            mode=SearchMode.LEXICAL,
            top_k=50,
        )
        assert results
        assert all(r.reference.period_key == "2025" for r in results)

    async def test_a_scope_filter_constrains(self, session) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="revenue",
            company_ids=[company_id],
            scope_types=["segment"],
            mode=SearchMode.LEXICAL,
            top_k=50,
        )
        assert results
        assert all(r.reference.scope_type == "segment" for r in results)
        assert any("Watchmakers" in r.text for r in results)

    async def test_one_companys_search_never_returns_anothers_evidence(
        self, session
    ) -> None:  # noqa: ANN001
        mine, _, backend = await _corpus(session)
        theirs, other_version, _ = await _corpus(session)
        await index_version(
            session,
            research_document_version_id=other_version.id,
            backend=backend,
            cfg=CFG,
        )
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="revenue",
            company_ids=[mine],
            mode=SearchMode.LEXICAL,
            top_k=50,
        )
        assert results
        assert all(r.reference.company_id == mine for r in results)

    async def test_a_source_tier_filter_constrains(self, session) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        assert (
            await search_corpus(
                session,
                backend=backend,
                cfg=CFG,
                query="revenue",
                company_ids=[company_id],
                source_tiers=["T4_quality_media"],
                mode=SearchMode.LEXICAL,
            )
            == []
        )

    async def test_a_deliberate_cross_entity_search_is_possible(self, session) -> None:  # noqa: ANN001
        mine, _, backend = await _corpus(session)
        _, other_version, _ = await _corpus(session)
        await index_version(
            session,
            research_document_version_id=other_version.id,
            backend=backend,
            cfg=CFG,
        )
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="revenue",
            mode=SearchMode.LEXICAL,
            allow_cross_entity=True,
            top_k=50,
        )
        assert len({r.reference.company_id for r in results}) == 2


# --------------------------------------------------------------------------- #
# Stable evidence identity
# --------------------------------------------------------------------------- #


class TestEvidenceResolution:
    def test_an_evidence_id_round_trips(self) -> None:
        assert evidence_id_for("c:abc").startswith(EVIDENCE_ID_PREFIX)
        assert chunk_id_from_evidence_id(evidence_id_for("c:abc")) == "c:abc"
        # Idempotent, so a caller that already has an evidence id is not punished.
        assert evidence_id_for(evidence_id_for("c:abc")) == evidence_id_for("c:abc")

    async def test_a_recorded_id_resolves_back_to_the_exact_span_and_its_page(
        self, session
    ) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="group revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        recorded = results[0].reference.evidence_id

        # ... months later, from nothing but the id.
        context = await resolve_evidence(session, evidence_id=recorded, cfg=CFG)
        assert context is not None
        assert context.text == results[0].text
        assert context.reference.page_start == results[0].reference.page_start
        assert context.reference.canonical_url == results[0].reference.canonical_url
        # "Show me where this number came from" renders the surrounding page.
        assert context.page_text is not None
        assert context.text in context.page_text

    async def test_a_table_evidence_id_resolves_to_the_grid_not_the_flat_text(
        self, session
    ) -> None:  # noqa: ANN001
        # The column -> period map is the part that makes a figure checkable, so
        # resolving a table citation must return the grid.
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="32,549 31,680",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        table_result = [r for r in results if r.reference.is_table][0]
        context = await resolve_evidence(
            session, evidence_id=table_result.reference.evidence_id, cfg=CFG
        )
        assert context is not None
        assert context.table_rows == [
            ["DKK million", "2025", "2024"],
            ["Revenue", "32,549", "31,680"],
        ]

    async def test_an_unknown_id_resolves_to_none_not_an_approximation(
        self, session
    ) -> None:  # noqa: ANN001
        assert await resolve_evidence(session, evidence_id="ev:c:nope", cfg=CFG) is None
        assert await resolve_evidence(session, evidence_id="", cfg=CFG) is None

    async def test_resolution_respects_the_corpus_flag(self, session) -> None:  # noqa: ANN001
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        assert (
            await resolve_evidence(
                session,
                evidence_id=results[0].reference.evidence_id,
                cfg=Settings(v3_corpus_enabled=False),
            )
            is None
        )

    async def test_an_id_survives_a_reindex(self, session) -> None:  # noqa: ANN001
        # The property that makes a citation durable: rebuilding the index puts
        # the same span back under the same id.
        company_id, version, backend = await _corpus(session)
        before = (
            await search_corpus(
                session,
                backend=backend,
                cfg=CFG,
                query="group revenue",
                company_ids=[company_id],
                mode=SearchMode.LEXICAL,
            )
        )[0].reference.evidence_id
        await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=CFG
        )
        after = (
            await search_corpus(
                session,
                backend=backend,
                cfg=CFG,
                query="group revenue",
                company_ids=[company_id],
                mode=SearchMode.LEXICAL,
            )
        )[0].reference.evidence_id
        assert before == after
        assert await resolve_evidence(session, evidence_id=before, cfg=CFG) is not None

    async def test_a_query_months_later_returns_the_right_page(self, session) -> None:  # noqa: ANN001
        # The V3.1 phase demonstration, in one test: ask for the segment figure and
        # get the page it is on, scoped as a segment, with citable lineage.
        company_id, _, backend = await _corpus(session)
        results = await search_corpus(
            session,
            backend=backend,
            cfg=CFG,
            query="specialist watchmakers revenue",
            company_ids=[company_id],
            mode=SearchMode.LEXICAL,
        )
        assert results
        top = results[0]
        assert "107" in top.text
        assert top.reference.page_start == 48
        assert top.reference.scope_type == "segment"
        assert top.reference.section_path == "Segment information"
        assert "p. 48" in top.reference.citation_label()
