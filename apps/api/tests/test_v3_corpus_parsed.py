"""The full parsed representation — V3.1 Slice 1.3.

WHAT THESE TESTS PIN
====================
V3's phase demonstration for the corpus starts "a real annual report ingests to
pages/sections/chunks" and ends "``DocumentTable`` survives a borderless
five-year summary". This file covers the first three of those.

  * the extractor's captured blocks become PAGES with the full text, not 20
    bounded excerpts;
  * offsets are exact — the full text reconstructs byte-for-byte and every page
    and section slices out of it correctly;
  * sections carry the heading path AND the resolved Group/segment scope, and an
    unrecognised heading is a segment rather than silently the Group;
  * tables survive as GRIDS with their column→period map intact;
  * a partial parse says ``partial`` — "we never read that page" must stay
    distinguishable from "that page does not say it";
  * a derivation is stamped with the pipeline version it ran under, exactly one
    is active, and a newer parser supersedes without destroying the old one;
  * `capture_blocks=False` leaves every V2 extraction byte-identical.

Everything DB-touching runs against a real SQLite database, so the partial unique
index that enforces "exactly one active derivation" is genuinely exercised.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
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
from app.models import research_derivation as _research_derivation  # noqa: F401
from app.models import research_document as _research_document  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.research_derivation import (
    DERIVATION_COMPLETE,
    DERIVATION_PARTIAL,
    ResearchDocumentDerivation,
    ResearchDocumentPage,
    ResearchDocumentSection,
    ResearchDocumentTable,
)
from app.models.research_document import ResearchDocumentVersion
from app.services.corpus.parsed import (
    PAGE_JOIN,
    DerivationResult,
    build_parsed_document,
    load_full_text,
    persist_parsed_document,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.primary_document_extractor import (
    ExtractedBlock,
    ExtractedTable,
    PrimaryDocumentExtraction,
    extract_html,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


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


def _blocks() -> list[ExtractedBlock]:
    return [
        ExtractedBlock(page_number=1, section="Group results", text="Group revenue rose."),
        ExtractedBlock(page_number=1, section="Group results", text="Operating margin held."),
        ExtractedBlock(
            page_number=2,
            section="Segment information",
            ancestor_heading="Financial statements",
            text="Jewellery Maisons revenue was 8,000.",
        ),
        ExtractedBlock(
            page_number=3,
            section="Segment information",
            ancestor_heading="Financial statements",
            text="Specialist Watchmakers revenue was 107.",
        ),
    ]


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
# The extractor change
# --------------------------------------------------------------------------- #


class TestBlockCapture:
    HTML = (
        b"<html><body><h1>Group results</h1><p>"
        + b"Revenue was DKK 23.0 billion. " * 10
        + b"</p><h2>Segment information</h2><p>"
        + b"Jewellery revenue was DKK 10 billion. " * 10
        + b"</p></body></html>"
    )

    def test_capture_is_off_by_default(self) -> None:
        assert extract_html(self.HTML, cfg=Settings()).blocks == []

    def test_capturing_changes_nothing_else_about_the_extraction(self) -> None:
        # The V2 output must be byte-identical with the corpus on or off, because
        # the same extraction still feeds the council, the validator and the report.
        cfg = Settings()
        without = extract_html(self.HTML, cfg=cfg)
        with_blocks = extract_html(self.HTML, cfg=cfg, capture_blocks=True)
        assert [e.model_dump() for e in without.excerpts] == [
            e.model_dump() for e in with_blocks.excerpts
        ]
        assert without.extracted_char_count == with_blocks.extracted_char_count
        assert without.status == with_blocks.status
        assert [t.model_dump() for t in without.tables] == [
            t.model_dump() for t in with_blocks.tables
        ]

    def test_captured_blocks_keep_the_heading_context(self) -> None:
        blocks = extract_html(self.HTML, cfg=Settings(), capture_blocks=True).blocks
        assert blocks
        assert {b.section for b in blocks} == {"Group results", "Segment information"}
        assert any(b.ancestor_heading == "Group results" for b in blocks)

    def test_capture_holds_far_more_than_the_excerpt_cap_allows(self) -> None:
        # 20 excerpts of <=1,200 chars is the limit the corpus exists to remove.
        cfg = Settings(primary_document_max_excerpts_per_document=2)
        result = extract_html(self.HTML, cfg=cfg, capture_blocks=True)
        assert len(result.excerpts) <= 2
        assert len(result.blocks) > len(result.excerpts)

    def test_a_pathological_block_is_capped_not_stored_whole(self) -> None:
        huge = (
            b"<html><body><h1>H</h1><p>" + (b"x" * 5000) + b"</p></body></html>"
        )
        result = extract_html(
            huge, cfg=Settings(v3_corpus_max_page_chars=500), capture_blocks=True
        )
        assert all(len(b.text) <= 500 for b in result.blocks)


# --------------------------------------------------------------------------- #
# The pure builder
# --------------------------------------------------------------------------- #


class TestBuildParsedDocument:
    def test_nothing_to_persist_returns_none(self) -> None:
        assert build_parsed_document(None) is None
        assert build_parsed_document(_extraction()) is None

    def test_blocks_become_pages_in_document_order(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        assert [p.page_number for p in parsed.pages] == [1, 2, 3]
        assert parsed.pages[0].text == "Group revenue rose.\n\nOperating margin held."

    def test_offsets_reconstruct_the_full_text_exactly(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        full = parsed.full_text()
        for page in parsed.pages:
            assert full[page.char_start : page.char_end] == page.text
        assert full == PAGE_JOIN.join(p.text for p in parsed.pages)

    def test_section_offsets_slice_out_of_the_same_text(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        full = parsed.full_text()
        for section in parsed.sections:
            span = full[section.char_start : section.char_end]
            assert span
            assert span.strip()

    def test_sections_follow_the_extractors_own_heading_runs(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        assert [s.heading for s in parsed.sections] == [
            "Group results",
            "Segment information",
        ]
        assert parsed.sections[1].heading_path == (
            "Financial statements > Segment information"
        )
        assert parsed.sections[1].page_start == 2
        assert parsed.sections[1].page_end == 3

    def test_a_group_heading_resolves_to_group_scope(self) -> None:
        parsed = build_parsed_document(
            _extraction(blocks=[ExtractedBlock(page_number=1, section="Group", text="x")])
        )
        assert parsed is not None
        assert parsed.sections[0].scope_type == "group"

    def test_a_segment_heading_resolves_to_a_named_segment(self) -> None:
        # Fail closed. Silently promoting a business-area heading to Group is how a
        # segment number ends up in a consolidated slot.
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        segment = parsed.sections[1]
        assert segment.scope_type == "segment"
        assert segment.scope_name == "Segment information"

    def test_a_heading_that_is_not_a_scope_signal_produces_no_scope(self) -> None:
        # Found by running a real 169-page Pandora annual report: sending raw
        # headings straight to ``parse_scope`` labelled "CONTENTS", "BIG" and
        # "PICTURE" as business segments, because its fail-closed rule — correct
        # for a string the extractor has already vetted — is "anything non-empty
        # is a named segment". A cover page is not a segment.
        blocks = [
            ExtractedBlock(page_number=1, section="CONTENTS", text="1 Introduction"),
            ExtractedBlock(page_number=2, section="BIG", text="Our year in numbers"),
            ExtractedBlock(page_number=3, section="THIS REPORT", text="About this report"),
        ]
        parsed = build_parsed_document(_extraction(blocks=blocks))
        assert parsed is not None
        assert [s.scope_type for s in parsed.sections] == [None, None, None]
        assert [s.scope_name for s in parsed.sections] == [None, None, None]

    def test_a_group_results_heading_is_group_not_a_segment_called_group(self) -> None:
        # The same defect in its other direction: "Group results" is not one of
        # ``GROUP_SCOPE_LABELS``, so ``parse_scope`` alone made it a SEGMENT named
        # "Group results" — a consolidated section filed under a segment scope.
        parsed = build_parsed_document(
            _extraction(
                blocks=[ExtractedBlock(page_number=1, section="Group results", text="x")]
            )
        )
        assert parsed is not None
        assert parsed.sections[0].scope_type == "group"

    def test_a_headingless_run_stays_unknown_scope(self) -> None:
        parsed = build_parsed_document(
            _extraction(blocks=[ExtractedBlock(page_number=1, text="Revenue rose.")])
        )
        assert parsed is not None
        assert parsed.sections[0].scope_type is None
        assert parsed.sections[0].scope_key is None

    def test_a_table_survives_as_a_grid_with_its_column_periods(self) -> None:
        # The Pandora five-year summary case: a borderless grid rebuilt
        # geometrically, whose value is entirely in the column -> year map.
        table = ExtractedTable(
            table_location="p12:t0",
            table_index=0,
            page_number=12,
            rows=[["", "2025", "2024", "2023"], ["Revenue", "31,7", "28,1", "26,5"]],
            row_count=2,
            col_count=4,
            reconstructed=True,
            column_periods=["2025", "2024", "2023"],
            scope="Group",
        )
        parsed = build_parsed_document(_extraction(blocks=_blocks(), tables=[table]))
        assert parsed is not None
        kept = parsed.tables[0]
        assert kept.rows == [
            ["", "2025", "2024", "2023"],
            ["Revenue", "31,7", "28,1", "26,5"],
        ]
        assert kept.column_periods == ["2025", "2024", "2023"]
        assert kept.reconstructed is True
        assert kept.scope_type == "group"
        assert kept.table_location == "p12:t0"

    def test_a_partial_parse_says_partial(self) -> None:
        # 4 pages persisted of a 169-page report. Saying "complete" would make
        # "we never read that page" indistinguishable from "it does not say it".
        parsed = build_parsed_document(_extraction(blocks=_blocks(), page_count=169))
        assert parsed is not None
        assert parsed.status == DERIVATION_PARTIAL

    def test_a_whole_document_says_complete(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks(), page_count=3))
        assert parsed is not None
        assert parsed.status == DERIVATION_COMPLETE

    def test_a_truncated_extraction_is_never_complete(self) -> None:
        parsed = build_parsed_document(
            _extraction(blocks=_blocks(), page_count=3, truncated=True)
        )
        assert parsed is not None
        assert parsed.status == DERIVATION_PARTIAL

    def test_an_unknown_page_count_is_not_treated_as_complete_coverage(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks(), page_count=None))
        assert parsed is not None
        assert parsed.page_count is None  # never backfilled from pages_persisted

    def test_a_page_cap_truncates_and_says_so(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()), max_pages=2)
        assert parsed is not None
        assert len(parsed.pages) == 2
        assert parsed.truncated is True
        assert parsed.status == DERIVATION_PARTIAL

    def test_an_html_document_is_recorded_as_unpaginated(self) -> None:
        parsed = build_parsed_document(
            _extraction(
                extraction_method="html",
                blocks=[ExtractedBlock(section="Body", text="Revenue rose.")],
            )
        )
        assert parsed is not None
        assert parsed.paginated is False
        assert parsed.pages[0].page_number == 1

    def test_a_pdf_is_recorded_as_paginated(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        assert parsed.paginated is True

    def test_the_derivation_is_stamped_with_the_current_pipeline_version(self) -> None:
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        assert parsed.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION

    def test_a_supplemental_page_keeps_its_position_in_reading_order(self) -> None:
        # The targeted supplemental pass appends pages far beyond the leading
        # window. Sorting them into numeric order would break the correspondence
        # between offsets and the sequence the extractor actually read.
        blocks = [
            ExtractedBlock(page_number=1, text="first"),
            ExtractedBlock(page_number=87, text="statements"),
            ExtractedBlock(page_number=2, text="second"),
        ]
        parsed = build_parsed_document(_extraction(blocks=blocks))
        assert parsed is not None
        assert [p.page_number for p in parsed.pages] == [1, 87, 2]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


async def _version(session) -> ResearchDocumentVersion:  # noqa: ANN001
    from app.models.research_document import ResearchDocument

    doc = ResearchDocument(
        id=uuid.uuid4(),
        document_key="annual_report:2025",
        document_type="annual_report",
        first_seen_at=T0,
        last_seen_at=T0,
    )
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=doc.id,
        content_hash="a" * 64,
        canonical_url="https://pandoragroup.com/ar2025.pdf",
        transport="company_ir",
        source_tier=T1_PRIMARY_FILING,
        access_class="public_issuer",
        retrieved_at=T0,
        extraction_status="extracted",
        is_current=True,
    )
    session.add(version)
    await session.flush()
    return version


class TestPersistence:
    async def test_the_corpus_flag_off_writes_nothing(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert (
            await persist_parsed_document(
                session,
                version_id=version.id,
                parsed=parsed,
                cfg=Settings(v3_corpus_enabled=False),
            )
            is None
        )
        assert (
            await session.execute(select(ResearchDocumentDerivation))
        ).scalars().all() == []

    async def test_pages_sections_and_tables_are_all_persisted(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        table = ExtractedTable(
            table_location="p12:t0",
            table_index=0,
            page_number=12,
            rows=[["Revenue", "31,7"]],
            row_count=1,
            col_count=2,
            column_periods=["2025"],
        )
        parsed = build_parsed_document(_extraction(blocks=_blocks(), tables=[table]))
        counts = DerivationResult()
        derivation = await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
            result=counts,
        )
        assert derivation is not None
        assert derivation.is_active is True
        assert (counts.pages_written, counts.sections_written, counts.tables_written) == (
            3,
            2,
            1,
        )
        assert derivation.pages_persisted == 3
        assert derivation.page_count == 169
        assert derivation.status == DERIVATION_PARTIAL

    async def test_the_full_text_reconstructs_from_the_database(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        assert parsed is not None
        derivation = await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
        )
        assert derivation is not None
        assert await load_full_text(session, derivation_id=derivation.id) == (
            parsed.full_text()
        )

    async def test_a_stored_page_is_the_whole_page_not_an_excerpt(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
        )
        page = (
            (
                await session.execute(
                    select(ResearchDocumentPage).where(
                        ResearchDocumentPage.page_number == 1
                    )
                )
            )
            .scalars()
            .one()
        )
        assert page.text == "Group revenue rose.\n\nOperating margin held."

    async def test_re_parsing_with_the_same_parser_is_idempotent(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        cfg = Settings(v3_corpus_enabled=True)
        first = await persist_parsed_document(
            session, version_id=version.id, parsed=parsed, cfg=cfg
        )
        counts = DerivationResult()
        second = await persist_parsed_document(
            session, version_id=version.id, parsed=parsed, cfg=cfg, result=counts
        )
        assert first is not None and second is not None
        assert first.id == second.id
        assert counts.derivations_created == 0
        assert counts.derivations_reused == 1
        pages = (await session.execute(select(ResearchDocumentPage))).scalars().all()
        assert len(pages) == 3

    async def test_a_newer_parser_supersedes_without_destroying_the_old(
        self, session
    ) -> None:  # noqa: ANN001
        version = await _version(session)
        cfg = Settings(v3_corpus_enabled=True)
        old = build_parsed_document(_extraction(blocks=_blocks()))
        assert old is not None
        old.pipeline_version = CURRENT_EXTRACTION_PIPELINE_VERSION - 1
        old_row = await persist_parsed_document(
            session, version_id=version.id, parsed=old, cfg=cfg
        )
        new = build_parsed_document(_extraction(blocks=_blocks()))
        counts = DerivationResult()
        new_row = await persist_parsed_document(
            session, version_id=version.id, parsed=new, cfg=cfg, result=counts
        )
        assert old_row is not None and new_row is not None
        await session.refresh(old_row)
        assert new_row.is_active is True
        assert old_row.is_active is False
        assert old_row.superseded_at is not None
        assert counts.superseded == 1
        # The old derivation's pages are STILL THERE — nothing is destroyed.
        rows = (await session.execute(select(ResearchDocumentDerivation))).scalars().all()
        assert len(rows) == 2
        old_pages = (
            (
                await session.execute(
                    select(ResearchDocumentPage).where(
                        ResearchDocumentPage.derivation_id == old_row.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(old_pages) == 3

    async def test_an_older_parser_never_rolls_the_corpus_back(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        cfg = Settings(v3_corpus_enabled=True)
        current = build_parsed_document(_extraction(blocks=_blocks()))
        current_row = await persist_parsed_document(
            session, version_id=version.id, parsed=current, cfg=cfg
        )
        stale = build_parsed_document(_extraction(blocks=_blocks()))
        assert stale is not None
        stale.pipeline_version = CURRENT_EXTRACTION_PIPELINE_VERSION - 3
        stale_row = await persist_parsed_document(
            session, version_id=version.id, parsed=stale, cfg=cfg
        )
        assert current_row is not None and stale_row is not None
        await session.refresh(current_row)
        assert current_row.is_active is True
        assert stale_row.is_active is False

    async def test_two_active_derivations_are_impossible(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
        )
        rogue = ResearchDocumentDerivation(
            id=uuid.uuid4(),
            research_document_version_id=version.id,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION + 5,
            extraction_method="native_pdf",
            status=DERIVATION_COMPLETE,
            is_active=True,
        )
        session.add(rogue)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_persisted_table_keeps_its_grid_and_period_map(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        table = ExtractedTable(
            table_location="p12:t0",
            table_index=0,
            page_number=12,
            rows=[["", "2025", "2024"], ["Revenue", "31,7", "28,1"]],
            row_count=2,
            col_count=3,
            reconstructed=True,
            column_periods=["2025", "2024"],
            scope="Jewellery Maisons",
        )
        parsed = build_parsed_document(_extraction(blocks=_blocks(), tables=[table]))
        await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
        )
        row = (await session.execute(select(ResearchDocumentTable))).scalars().one()
        assert row.rows_json == [["", "2025", "2024"], ["Revenue", "31,7", "28,1"]]
        assert row.column_periods == ["2025", "2024"]
        assert row.reconstructed is True
        assert row.scope_type == "segment"
        assert row.scope_name == "Jewellery Maisons"

    async def test_sections_carry_scope_to_the_database(self, session) -> None:  # noqa: ANN001
        version = await _version(session)
        parsed = build_parsed_document(_extraction(blocks=_blocks()))
        await persist_parsed_document(
            session,
            version_id=version.id,
            parsed=parsed,
            cfg=Settings(v3_corpus_enabled=True),
        )
        sections = (
            (
                await session.execute(
                    select(ResearchDocumentSection).order_by(
                        ResearchDocumentSection.section_index
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [s.scope_type for s in sections] == ["group", "segment"]
        assert sections[1].heading_path == "Financial statements > Segment information"


class TestEndToEndIngestion:
    async def test_a_real_html_document_ingests_to_pages_and_sections(
        self, session
    ) -> None:  # noqa: ANN001
        """The slice's own end-to-end path: bytes → extraction → corpus structure."""
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.artifacts.service import store_raw_artifact
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact

        html = (
            b"<html><body><h1>Group results</h1><p>"
            + b"Group revenue was DKK 31.7 billion in 2025. " * 8
            + b"</p><h2>Segment information</h2><p>"
            + b"Jewellery revenue was DKK 10.0 billion. " * 8
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
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/annual-report-2025",
            status="extracted",
            title="Annual Report 2025",
            doc_kind="annual_report",
            extraction=extraction,
            raw_artifact=stored,
        )
        await persist_primary_document_artifacts(
            session,
            artifacts=[artifact],
            company_id=uuid.uuid4(),
            agent_run_id=None,
            cfg=cfg,
        )
        derivation = (
            (await session.execute(select(ResearchDocumentDerivation))).scalars().one()
        )
        assert derivation.is_active is True
        assert derivation.paginated is False
        assert derivation.pages_persisted == 1
        pages = (await session.execute(select(ResearchDocumentPage))).scalars().all()
        assert len(pages) == 1
        # The whole body survived, not 20 bounded excerpts of it.
        assert "Jewellery revenue was DKK 10.0 billion." in pages[0].text
        assert "Group revenue was DKK 31.7 billion in 2025." in pages[0].text
        sections = (await session.execute(select(ResearchDocumentSection))).scalars().all()
        assert {s.heading for s in sections} == {"Group results", "Segment information"}

    async def test_with_the_corpus_off_no_derivation_is_written(self, session) -> None:  # noqa: ANN001
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact

        cfg = Settings(
            v3_corpus_enabled=False,
            primary_document_ingestion_enabled=True,
            report_citation_persistence_enabled=True,
        )
        artifact = PrimaryDocumentArtifact(
            source_url="https://pandoragroup.com/ar.pdf",
            status="extracted",
            extraction=_extraction(blocks=_blocks()),
        )
        await persist_primary_document_artifacts(
            session,
            artifacts=[artifact],
            company_id=uuid.uuid4(),
            agent_run_id=None,
            cfg=cfg,
        )
        assert (
            await session.execute(select(ResearchDocumentDerivation))
        ).scalars().all() == []
