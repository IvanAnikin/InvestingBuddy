"""Document-aware chunking and indexing — V3.1 Slice 1.5.

WHAT THESE TESTS PIN
====================
  * a chunk NEVER crosses a section boundary — a chunk that starts in "Group
    results" and ends in "Jewellery Maisons" carries two scopes and can be cited
    as either, which is a scope error baked into the retrieval unit;
  * a page boundary is preferred once there is something worth citing;
  * a sentence is never cut in half;
  * a table is its own chunk and POINTS AT the stored grid rather than replacing
    it;
  * chunk ids are stable across re-runs of the same parser, and deliberately
    different under a new one;
  * every filter key a search needs is on the chunk, so filters apply inside the
    search;
  * governance is stamped at persistence, not checked at query time;
  * chunks are corpus data: they exist with no search backend configured at all.

The DB-touching tests run against real SQLite, and the end-to-end test indexes
into the real in-memory backend rather than a mock.
"""

from __future__ import annotations

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
from app.models.research_chunk import (
    CHUNK_KIND_PROSE,
    CHUNK_KIND_TABLE,
    ResearchDocumentChunk,
)
from app.models.research_derivation import ResearchDocumentTable
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.chunking import (
    build_chunks,
    chunk_identity,
    render_table,
)
from app.services.corpus.indexing import (
    ChunkingResult,
    drop_chunks_for_derivation,
    index_version,
    persist_chunks,
    to_corpus_chunks,
)
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.types import CorpusFilters, CorpusQuery, SearchMode
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.primary_document_extractor import (
    ExtractedBlock,
    ExtractedTable,
    PrimaryDocumentExtraction,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
VERSION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _extraction(**overrides: object) -> PrimaryDocumentExtraction:
    base: dict[str, object] = {
        "content_hash": "a" * 64,
        "mime_type": "application/pdf",
        "extraction_method": "native_pdf",
        "status": "extracted",
        "page_count": 169,
        "language": "en",
    }
    base.update(overrides)
    return PrimaryDocumentExtraction(**base)  # type: ignore[arg-type]


def _para(prefix: str, n: int = 12) -> str:
    return " ".join(f"{prefix} sentence {i} about revenue and margin." for i in range(n))


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


# --------------------------------------------------------------------------- #
# Chunk identity
# --------------------------------------------------------------------------- #


class TestChunkIdentity:
    def test_the_same_span_of_the_same_parse_gets_the_same_id(self) -> None:
        args = dict(
            research_document_version_id=VERSION_ID,
            pipeline_version=15,
            kind=CHUNK_KIND_PROSE,
            ordinal=3,
            char_start=100,
            char_end=1300,
        )
        assert chunk_identity(**args) == chunk_identity(**args)  # type: ignore[arg-type]

    def test_a_different_parser_version_is_a_different_chunk(self) -> None:
        # Correct: it is a different reading of the document. The old derivation
        # and its chunks are retained, so the old citation still resolves.
        base = dict(
            research_document_version_id=VERSION_ID,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=0,
            char_end=100,
        )
        assert chunk_identity(pipeline_version=14, **base) != chunk_identity(  # type: ignore[arg-type]
            pipeline_version=15, **base  # type: ignore[arg-type]
        )

    def test_the_id_is_not_a_row_id(self) -> None:
        # A row id belongs to whichever database wrote it, so rebuilding the
        # corpus would invalidate every citation.
        identity = chunk_identity(
            research_document_version_id=VERSION_ID,
            pipeline_version=15,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=0,
            char_end=10,
        )
        assert identity.startswith("c:")
        assert len(identity) <= 80

    def test_a_different_extraction_profile_is_a_different_chunk(self) -> None:
        # Found by a real 169-page annual report: a DEEP reprocess re-reads the
        # same leading pages, so without the profile in the key its first chunks
        # share an ordinal and an offset range with the live parse's and hash to
        # the same id — two derivations, one chunk id, UNIQUE violation.
        from app.models.research_derivation import PROFILE_DEEP, PROFILE_LIVE

        base = dict(
            research_document_version_id=VERSION_ID,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=0,
            char_end=1139,
        )
        assert chunk_identity(extraction_profile=PROFILE_LIVE, **base) != chunk_identity(  # type: ignore[arg-type]
            extraction_profile=PROFILE_DEEP, **base  # type: ignore[arg-type]
        )

    def test_two_documents_never_share_a_chunk_id(self) -> None:
        a = chunk_identity(
            research_document_version_id=uuid.uuid4(),
            pipeline_version=15,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=0,
            char_end=10,
        )
        b = chunk_identity(
            research_document_version_id=uuid.uuid4(),
            pipeline_version=15,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=0,
            char_end=10,
        )
        assert a != b


# --------------------------------------------------------------------------- #
# The chunking strategy
# --------------------------------------------------------------------------- #


class TestChunking:
    def test_a_short_section_is_one_chunk(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[ExtractedBlock(page_number=1, section="Group", text="Revenue rose.")]
            )
        )
        assert parsed is not None
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        assert len(chunks) == 1
        assert chunks[0].text == "Revenue rose."

    def test_a_chunk_never_crosses_a_section_boundary(self) -> None:
        # The hard rule. A chunk spanning two sections carries two scopes and can
        # be cited as either.
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Group results", text="Group revenue."),
                    ExtractedBlock(
                        page_number=1, section="Jewellery Maisons", text="Jewellery revenue."
                    ),
                ]
            )
        )
        assert parsed is not None
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        assert len(chunks) == 2
        assert chunks[0].section_path == "Group results"
        assert chunks[1].section_path == "Jewellery Maisons"
        for chunk in chunks:
            assert "Group revenue." not in chunk.text or "Jewellery" not in chunk.text

    def test_each_chunk_carries_its_own_sections_scope(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Group", text="Group revenue."),
                    ExtractedBlock(
                        page_number=2, section="Segment information", text="Segment revenue."
                    ),
                ]
            )
        )
        assert parsed is not None
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        assert [c.scope_type for c in chunks] == ["group", "segment"]

    def test_a_long_section_is_split_and_stays_within_the_hard_maximum(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Review", text=_para("A", 60)),
                ]
            )
        )
        assert parsed is not None
        cfg = Settings(v3_corpus_chunk_target_chars=400, v3_corpus_chunk_max_chars=600)
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID, cfg=cfg)
        assert len(chunks) > 1
        assert all(len(c.text) <= 600 for c in chunks)

    def test_a_split_section_covers_its_whole_span_with_no_gaps_or_overlap(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[ExtractedBlock(page_number=1, section="Review", text=_para("A", 60))]
            )
        )
        assert parsed is not None
        cfg = Settings(v3_corpus_chunk_target_chars=400, v3_corpus_chunk_max_chars=600)
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID, cfg=cfg)
        section = parsed.sections[0]
        assert chunks[0].char_start == section.char_start
        assert chunks[-1].char_end == section.char_end
        for earlier, later in zip(chunks, chunks[1:]):
            # Contiguous: no gap (evidence would be unreachable) and no overlap
            # (one sentence would produce two hits and an ambiguous citation).
            assert earlier.char_end == later.char_start

    def test_a_page_boundary_is_preferred_once_there_is_something_worth_citing(
        self,
    ) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Review", text=_para("P1", 40)),
                    ExtractedBlock(page_number=2, section="Review", text=_para("P2", 40)),
                ]
            )
        )
        assert parsed is not None
        cfg = Settings(
            v3_corpus_chunk_target_chars=800,
            v3_corpus_chunk_max_chars=4000,
            v3_corpus_chunk_min_chars=200,
        )
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID, cfg=cfg)
        # No chunk mixes the two pages' text, because the page boundary was taken.
        for chunk in chunks:
            assert not ("P1 sentence" in chunk.text and "P2 sentence" in chunk.text)

    def test_every_chunk_records_the_pages_it_spans(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=7, section="Review", text="Revenue rose."),
                ]
            )
        )
        assert parsed is not None
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        assert chunks[0].page_start == 7
        assert chunks[0].page_end == 7

    def test_offsets_slice_the_chunk_back_out_of_the_document(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="A", text=_para("A", 30)),
                    ExtractedBlock(page_number=2, section="B", text=_para("B", 30)),
                ]
            )
        )
        assert parsed is not None
        full = parsed.full_text()
        for chunk in build_chunks(parsed, research_document_version_id=VERSION_ID):
            if chunk.kind == CHUNK_KIND_PROSE:
                assert chunk.text in full[chunk.char_start : chunk.char_end]

    def test_a_sentence_is_never_cut_in_half_at_a_paragraph_split(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Review", text=_para("A", 20)),
                    ExtractedBlock(page_number=1, section="Review", text=_para("B", 20)),
                    ExtractedBlock(page_number=1, section="Review", text=_para("C", 20)),
                ]
            )
        )
        assert parsed is not None
        cfg = Settings(v3_corpus_chunk_target_chars=600, v3_corpus_chunk_max_chars=1200)
        for chunk in build_chunks(parsed, research_document_version_id=VERSION_ID, cfg=cfg):
            assert chunk.text.endswith(".")

    def test_there_is_deliberately_no_overlap_setting(self) -> None:
        # Overlap is the patch for fixed-width splitting cutting through meaning.
        # A chunker that breaks at paragraph boundaries does not need it, and two
        # chunks sharing a sentence would produce two hits for one piece of
        # evidence and a citation that could name either.
        assert not any("chunk_overlap" in f for f in Settings.model_fields)


class TestTableChunks:
    def _parsed_with_table(self):  # noqa: ANN202
        table = ExtractedTable(
            table_location="p14:m0",
            table_index=0,
            page_number=14,
            rows=[
                ["DKK million", "2025", "2024", "2023"],
                ["Revenue", "32,549", "31,680", "28,136"],
            ],
            row_count=2,
            col_count=4,
            reconstructed=True,
            column_periods=["2025", "2024", "2023"],
            scope="Group",
        )
        return build_parsed_document(
            _extraction(
                blocks=[ExtractedBlock(page_number=1, section="Group", text="Revenue rose.")],
                tables=[table],
            )
        )

    def test_a_table_is_its_own_chunk_never_merged_into_prose(self) -> None:
        parsed = self._parsed_with_table()
        assert parsed is not None
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        kinds = [c.kind for c in chunks]
        assert CHUNK_KIND_TABLE in kinds
        prose = [c for c in chunks if c.kind == CHUNK_KIND_PROSE]
        assert all("32,549" not in c.text for c in prose)

    def test_a_table_chunk_leads_with_its_column_periods(self) -> None:
        # "revenue 32,549" is not evidence; "FY2025 revenue 32,549" is. A hit is
        # often read without opening the grid behind it.
        parsed = self._parsed_with_table()
        assert parsed is not None
        table_chunk = [
            c
            for c in build_chunks(parsed, research_document_version_id=VERSION_ID)
            if c.kind == CHUNK_KIND_TABLE
        ][0]
        assert table_chunk.text.startswith("Periods: 2025, 2024, 2023")
        assert "32,549" in table_chunk.text

    def test_a_table_chunk_carries_its_location_and_page(self) -> None:
        parsed = self._parsed_with_table()
        assert parsed is not None
        table_chunk = [
            c
            for c in build_chunks(parsed, research_document_version_id=VERSION_ID)
            if c.kind == CHUNK_KIND_TABLE
        ][0]
        assert table_chunk.table_location == "p14:m0"
        assert table_chunk.page_start == 14

    def test_a_table_chunk_carries_the_tables_scope(self) -> None:
        parsed = self._parsed_with_table()
        assert parsed is not None
        table_chunk = [
            c
            for c in build_chunks(parsed, research_document_version_id=VERSION_ID)
            if c.kind == CHUNK_KIND_TABLE
        ][0]
        assert table_chunk.scope_type == "group"

    def test_rendering_a_table_is_bounded(self) -> None:
        from app.services.corpus.parsed import ParsedTable

        huge = ParsedTable(
            table_index=0,
            table_location="t0",
            page_number=None,
            rows=[["x" * 100] * 10 for _ in range(200)],
            row_count=200,
            col_count=10,
            reconstructed=False,
            column_periods=[],
            scope_type=None,
            scope_name=None,
            scope_key=None,
            extraction_method="html",
            confidence=None,
        )
        assert len(render_table(huge, max_chars=500)) <= 500


# --------------------------------------------------------------------------- #
# Persistence and indexing
# --------------------------------------------------------------------------- #


async def _seed(session, **version_overrides):  # noqa: ANN001, ANN202
    document = ResearchDocument(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        document_key="annual_report:2025",
        document_type="annual_report",
        first_seen_at=T0,
        last_seen_at=T0,
    )
    session.add(document)
    await session.flush()
    payload: dict[str, object] = {
        "id": uuid.uuid4(),
        "research_document_id": document.id,
        "content_hash": "a" * 64,
        "canonical_url": "https://pandoragroup.com/ar2025.pdf",
        "transport": "company_ir",
        "source_tier": T1_PRIMARY_FILING,
        "access_class": "public_issuer",
        "retrieved_at": T0,
        "extraction_status": "extracted",
        "is_current": True,
        "period_key": "2025",
        "period_type": "annual",
        "language": "en",
        "title": "Annual Report 2025",
        "published_at": date(2026, 2, 4),
    }
    payload.update(version_overrides)
    version = ResearchDocumentVersion(**payload)  # type: ignore[arg-type]
    session.add(version)
    await session.flush()
    return document, version


class TestPersistence:
    async def _persist(self, session, cfg=None, **version_overrides):  # noqa: ANN001, ANN202
        cfg = cfg or Settings(v3_corpus_enabled=True)
        document, version = await _seed(session, **version_overrides)
        table = ExtractedTable(
            table_location="p14:m0",
            table_index=0,
            page_number=14,
            rows=[["DKK million", "2025"], ["Revenue", "32,549"]],
            row_count=2,
            col_count=2,
            reconstructed=True,
            column_periods=["2025"],
            scope="Group",
        )
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Group results", text=_para("G", 20)),
                    ExtractedBlock(
                        page_number=2, section="Segment information", text=_para("S", 20)
                    ),
                ],
                tables=[table],
            )
        )
        derivation = await persist_parsed_document(
            session, version_id=version.id, parsed=parsed, cfg=cfg
        )
        assert derivation is not None and parsed is not None
        counts = ChunkingResult()
        rows = await persist_chunks(
            session,
            version=version,
            derivation=derivation,
            parsed=parsed,
            cfg=cfg,
            result=counts,
        )
        return document, version, derivation, rows, counts

    async def test_chunks_are_persisted_with_every_filter_key(self, session) -> None:  # noqa: ANN001
        document, version, _, rows, counts = await self._persist(session)
        assert counts.chunks_created == len(rows) > 0
        for row in rows:
            assert row.company_id == document.company_id
            assert row.document_type == "annual_report"
            assert row.source_tier == T1_PRIMARY_FILING
            assert row.access_class == "public_issuer"
            assert row.period_key == "2025"
            assert row.period_type == "annual"
            assert row.language == "en"
            assert row.published_at == date(2026, 2, 4)

    async def test_a_table_chunk_points_at_the_stored_grid(self, session) -> None:  # noqa: ANN001
        # The grid is the truth; the chunk is how it is found. A hit must resolve
        # to the grid, never to the flattened text it was found by.
        _, _, derivation, rows, counts = await self._persist(session)
        table_row = [r for r in rows if r.kind == CHUNK_KIND_TABLE][0]
        assert table_row.research_document_table_id is not None
        assert counts.tables_linked == 1
        grid = await session.get(ResearchDocumentTable, table_row.research_document_table_id)
        assert grid is not None
        assert grid.rows_json == [["DKK million", "2025"], ["Revenue", "32,549"]]
        assert grid.column_periods == ["2025"]

    async def test_persisting_twice_reuses_rather_than_duplicating(self, session) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        _, version, derivation, rows, _ = await self._persist(session)
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Group results", text=_para("G", 20)),
                ]
            )
        )
        counts = ChunkingResult()
        again = await persist_chunks(
            session,
            version=version,
            derivation=derivation,
            parsed=parsed,  # type: ignore[arg-type]
            cfg=cfg,
            result=counts,
        )
        assert counts.chunks_created == 0
        assert counts.chunks_reused == len(rows)
        assert {r.id for r in again} == {r.id for r in rows}

    async def test_the_corpus_flag_off_persists_nothing(self, session) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        _, version, derivation, _, _ = await self._persist(session)
        await drop_chunks_for_derivation(session, derivation_id=derivation.id)
        parsed = build_parsed_document(
            _extraction(blocks=[ExtractedBlock(page_number=1, section="A", text="x")])
        )
        rows = await persist_chunks(
            session,
            version=version,
            derivation=derivation,
            parsed=parsed,  # type: ignore[arg-type]
            cfg=Settings(v3_corpus_enabled=False),
        )
        assert rows == []
        assert (await session.execute(select(ResearchDocumentChunk))).scalars().all() == []
        assert cfg.v3_corpus_enabled  # the fixture's config is unrelated

    async def test_governance_is_stamped_at_persistence_not_checked_later(
        self, session
    ) -> None:  # noqa: ANN001
        # A policy check that only happens on the way out is one that whichever
        # read path forgets it will skip.
        _, _, _, rows, _ = await self._persist(session, access_class="licensed_private")
        assert rows
        assert all(row.indexable is False for row in rows)

    async def test_two_derivations_of_one_version_can_both_hold_chunks(
        self, session
    ) -> None:  # noqa: ANN001
        # The regression a real document found: a deep reprocess re-reads the same
        # leading pages, so its first chunks would collide with the live parse's
        # unless the extraction profile is part of the identity.
        from app.models.research_derivation import PROFILE_DEEP

        cfg = Settings(v3_corpus_enabled=True)
        _, version, live_derivation, live_rows, _ = await self._persist(session)
        parsed = build_parsed_document(
            _extraction(
                blocks=[
                    ExtractedBlock(page_number=1, section="Group results", text=_para("G", 20)),
                    ExtractedBlock(
                        page_number=2, section="Segment information", text=_para("S", 20)
                    ),
                    ExtractedBlock(page_number=3, section="Notes", text=_para("N", 20)),
                ]
            ),
            extraction_profile=PROFILE_DEEP,
        )
        deep_derivation = await persist_parsed_document(
            session, version_id=version.id, parsed=parsed, cfg=cfg
        )
        assert deep_derivation is not None and parsed is not None
        deep_rows = await persist_chunks(
            session,
            version=version,
            derivation=deep_derivation,
            parsed=parsed,
            cfg=cfg,
        )
        assert deep_rows
        assert live_derivation.id != deep_derivation.id
        live_ids = {r.chunk_id for r in live_rows}
        deep_ids = {r.chunk_id for r in deep_rows}
        assert live_ids.isdisjoint(deep_ids)
        stored = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert len(stored) == len(live_rows) + len(deep_rows)

    async def test_dropping_one_derivations_chunks_leaves_another_alone(
        self, session
    ) -> None:  # noqa: ANN001
        _, _, derivation, rows, _ = await self._persist(session)
        assert await drop_chunks_for_derivation(session, derivation_id=derivation.id) == len(
            rows
        )
        assert await drop_chunks_for_derivation(session, derivation_id=uuid.uuid4()) == 0


class TestIndexing:
    async def test_chunks_exist_with_no_search_backend_configured(self, session) -> None:  # noqa: ANN001
        # Chunks are corpus DATA. That is what makes OPEN DECISION #1 cheap to
        # defer: switching backends reindexes from rows that are already there.
        _, _, _, rows, _ = await TestPersistence()._persist(session)
        assert rows
        stored = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert len(stored) == len(rows)

    async def test_indexing_makes_a_document_searchable_with_full_lineage(
        self, session
    ) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        document, version, _, _, _ = await TestPersistence()._persist(session)
        backend = InMemorySearchBackend()
        result = await index_version(
            session,
            research_document_version_id=version.id,
            backend=backend,
            cfg=cfg,
        )
        assert result.indexed > 0
        hits = await backend.search(
            CorpusQuery(
                text="revenue",
                mode=SearchMode.LEXICAL,
                filters=CorpusFilters(
                    company_ids=(document.company_id,), period_keys=("2025",)
                ),
            )
        )
        assert hits
        hit = hits[0].chunk
        # Every citation field is present WITHOUT a second lookup.
        assert hit.canonical_url == "https://pandoragroup.com/ar2025.pdf"
        assert hit.title == "Annual Report 2025"
        assert hit.research_document_id == document.id
        assert hit.research_document_version_id == version.id
        assert hit.period_key == "2025"
        assert hit.source_tier == T1_PRIMARY_FILING
        assert hit.page_start is not None

    async def test_a_table_is_findable_and_resolves_to_its_grid(self, session) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        document, version, _, rows, _ = await TestPersistence()._persist(session)
        backend = InMemorySearchBackend()
        await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        hits = await backend.search(
            CorpusQuery(
                text="32,549",
                mode=SearchMode.LEXICAL,
                filters=CorpusFilters(company_ids=(document.company_id,)),
            )
        )
        assert hits
        found = hits[0].chunk
        assert found.table_location == "p14:m0"
        row = [r for r in rows if r.chunk_id == found.chunk_id][0]
        assert row.research_document_table_id is not None

    async def test_indexing_is_idempotent(self, session) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        _, version, _, rows, _ = await TestPersistence()._persist(session)
        backend = InMemorySearchBackend()
        await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        second = await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        # A replace deletes first, so every chunk is a fresh insert rather than an
        # update — and there is exactly one copy of each.
        assert second.indexed == len(rows)
        assert await backend.delete(research_document_version_id=version.id) == len(rows)

    async def test_a_non_indexable_document_is_stored_but_never_searchable(
        self, session
    ) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        _, version, _, rows, _ = await TestPersistence()._persist(
            session, access_class="licensed_private"
        )
        backend = InMemorySearchBackend()
        result = await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        assert rows
        assert result.indexed == 0
        assert result.skipped_not_indexable == len(rows)

    async def test_a_version_with_no_active_derivation_is_not_indexed(
        self, session
    ) -> None:  # noqa: ANN001
        # Indexing a superseded derivation would put a stale reading of the
        # document in front of a researcher.
        cfg = Settings(v3_corpus_enabled=True)
        _, version, derivation, _, _ = await TestPersistence()._persist(session)
        derivation.is_active = False
        await session.flush()
        backend = InMemorySearchBackend()
        result = await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        assert result.indexed == 0

    async def test_an_unknown_version_indexes_nothing_rather_than_raising(
        self, session
    ) -> None:  # noqa: ANN001
        result = await index_version(
            session,
            research_document_version_id=uuid.uuid4(),
            backend=InMemorySearchBackend(),
            cfg=Settings(v3_corpus_enabled=True),
        )
        assert result.indexed == 0

    async def test_the_corpus_flag_off_indexes_nothing(self, session) -> None:  # noqa: ANN001
        _, version, _, _, _ = await TestPersistence()._persist(session)
        result = await index_version(
            session,
            research_document_version_id=version.id,
            backend=InMemorySearchBackend(),
            cfg=Settings(v3_corpus_enabled=False),
        )
        assert result.indexed == 0

    async def test_one_companys_search_never_returns_anothers_chunks(
        self, session
    ) -> None:  # noqa: ANN001
        cfg = Settings(v3_corpus_enabled=True)
        backend = InMemorySearchBackend()
        mine = None
        for _ in range(2):
            document, version, _, _, _ = await TestPersistence()._persist(session)
            mine = mine or document
            await index_version(
                session, research_document_version_id=version.id, backend=backend, cfg=cfg
            )
        assert mine is not None
        hits = await backend.search(
            CorpusQuery(
                text="revenue",
                mode=SearchMode.LEXICAL,
                filters=CorpusFilters(company_ids=(mine.company_id,)),
                top_k=50,
            )
        )
        assert hits
        assert all(h.chunk.company_id == mine.company_id for h in hits)

    async def test_stored_rows_convert_to_the_search_contract(self, session) -> None:  # noqa: ANN001
        _, version, _, rows, _ = await TestPersistence()._persist(session)
        converted = to_corpus_chunks(rows, version=version)
        assert len(converted) == len(rows)
        assert {c.chunk_id for c in converted} == {r.chunk_id for r in rows}


class TestEndToEnd:
    async def test_ingestion_produces_searchable_chunks(self, session) -> None:  # noqa: ANN001
        """Bytes → extraction → corpus → chunks, through the real ingestion path."""
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.artifacts.service import store_raw_artifact
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
        from app.services.sources.primary_document_extractor import extract_html

        html = (
            b"<html><body><h1>Group results</h1><p>"
            + b"Group revenue was DKK 32,549 million in 2025. " * 6
            + b"</p><h2>Segment information</h2><p>"
            + b"Jewellery revenue was DKK 10,000 million. " * 6
            + b"</p></body></html>"
        )
        cfg = Settings(
            v3_corpus_enabled=True,
            v3_artifact_store_backend="memory",
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        extraction = extract_html(html, cfg=cfg, capture_blocks=True)
        stored = await store_raw_artifact(
            html,
            media_type="text/html",
            access_class="public_issuer",
            cfg=cfg,
            store=InMemoryArtifactStore(),
        )
        assert stored is not None
        extraction.content_hash = stored.content_hash
        company_id = uuid.uuid4()
        await persist_primary_document_artifacts(
            session,
            artifacts=[
                PrimaryDocumentArtifact(
                    source_url="https://pandoragroup.com/annual-report-2025",
                    status="extracted",
                    title="Annual Report 2025",
                    doc_kind="annual_report",
                    extraction=extraction,
                    raw_artifact=stored,
                )
            ],
            company_id=company_id,
            agent_run_id=None,
            cfg=cfg,
        )
        chunks = (await session.execute(select(ResearchDocumentChunk))).scalars().all()
        assert chunks
        assert all(c.company_id == company_id for c in chunks)

        version = (
            (await session.execute(select(ResearchDocumentVersion))).scalars().one()
        )
        backend = InMemorySearchBackend()
        await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        hits = await backend.search(
            CorpusQuery(
                text="jewellery revenue",
                mode=SearchMode.LEXICAL,
                filters=CorpusFilters(company_ids=(company_id,)),
            )
        )
        assert hits
        assert "Jewellery" in hits[0].chunk.text
        # And it is scoped as a segment, not as the Group.
        assert hits[0].chunk.scope_type == "segment"

    def test_the_pipeline_version_is_what_chunk_ids_are_stamped_with(self) -> None:
        parsed = build_parsed_document(
            _extraction(blocks=[ExtractedBlock(page_number=1, section="A", text="x")])
        )
        assert parsed is not None
        assert parsed.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION
        chunks = build_chunks(parsed, research_document_version_id=VERSION_ID)
        assert chunks[0].chunk_id == chunk_identity(
            research_document_version_id=VERSION_ID,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
            kind=CHUNK_KIND_PROSE,
            ordinal=0,
            char_start=chunks[0].char_start,
            char_end=chunks[0].char_end,
        )
