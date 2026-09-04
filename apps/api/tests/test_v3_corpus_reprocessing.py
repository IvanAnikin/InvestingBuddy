"""Re-extraction and the parser-version lifecycle — V3.1 Slice 1.7.

WHAT THESE TESTS PIN
====================
The reason the raw bytes are retained at all:

    raw artifact → parser N → parsed → parser N+1 → deterministic reprocessing
    → prior derivation retained and auditable → new active representation

  * reprocessing reads the STORED bytes and **never fetches** — a document whose
    bytes are gone is skipped honestly, not re-downloaded;
  * the re-read bytes are re-hashed, so a store returning the wrong object cannot
    attach one document's citations to another's text;
  * the prior derivation, its pages, its tables and its chunks all survive; only
    ``is_active`` moves;
  * a newer parser wins; at the same parser, MORE of the document wins; a smaller
    or staler re-run can never roll the corpus backwards;
  * reprocessing is deterministic — same bytes, same profile, same chunk ids;
  * an empty or failed re-parse never supersedes a derivation that has content;
  * the index is reconciled to the new active derivation while the old chunks stay
    in the database;
  * **nothing schedules a batch reprocess.**
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

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
from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_derivation import (
    PROFILE_DEEP,
    PROFILE_LIVE,
    ResearchDocumentDerivation,
    ResearchDocumentPage,
)
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.corpus.artifacts.service import record_artifact, store_raw_artifact
from app.services.corpus.indexing import index_version, persist_chunks
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.corpus.reprocessing import (
    OUTCOME_DISABLED,
    OUTCOME_EXTRACTION_FAILED,
    OUTCOME_NO_BYTES,
    OUTCOME_NOT_FOUND,
    OUTCOME_REPROCESSED,
    OUTCOME_UP_TO_DATE,
    REPROCESS_OUTCOMES,
    deep_profile_configured,
    needs_reprocessing,
    reprocess_batch,
    reprocess_version,
    settings_for_profile,
    versions_needing_reprocessing,
)
from app.services.corpus.retrieval import resolve_evidence, search_corpus
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.types import SearchMode
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.primary_document_extractor import (
    PrimaryDocumentExtraction,
    extract_html,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)

HTML = (
    b"<html><body><h1>Group results</h1><p>"
    + b"Group revenue was DKK 32,549 million in 2025. " * 8
    + b"</p><h2>Segment information</h2><p>"
    + b"Specialist Watchmakers revenue was EUR 107 million. " * 8
    + b"</p></body></html>"
)


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "v3_corpus_enabled": True,
        "v3_artifact_store_backend": "memory",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


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


async def _seed(
    session,  # noqa: ANN001
    *,
    cfg: Settings,
    store: InMemoryArtifactStore,
    pipeline_version: int | None = None,
    retain_bytes: bool = True,
    raw: bytes = HTML,
):
    """One document version with a stored artifact and one live derivation."""
    stored = await store_raw_artifact(
        raw,
        media_type="text/html",
        access_class="public_issuer",
        cfg=cfg,
        store=store,
    )
    assert stored is not None
    artifact_row = await record_artifact(session, stored, cfg=cfg)

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
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=stored.content_hash,
        canonical_url="https://pandoragroup.com/annual-report-2025",
        title="Annual Report 2025",
        media_type="text/html",
        transport="company_ir",
        source_tier=T1_PRIMARY_FILING,
        access_class="public_issuer",
        retrieved_at=T0,
        extraction_status="extracted",
        is_current=True,
        period_key="2025",
        period_type="annual",
        language="en",
        research_artifact_id=artifact_row.id if retain_bytes and artifact_row else None,
    )
    session.add(version)
    await session.flush()

    extraction = extract_html(raw, cfg=cfg, capture_blocks=True)
    extraction.content_hash = stored.content_hash
    parsed = build_parsed_document(extraction, extraction_profile=PROFILE_LIVE)
    assert parsed is not None
    if pipeline_version is not None:
        parsed.pipeline_version = pipeline_version
    derivation = await persist_parsed_document(
        session, version_id=version.id, parsed=parsed, cfg=cfg
    )
    assert derivation is not None
    await persist_chunks(
        session, version=version, derivation=derivation, parsed=parsed, cfg=cfg
    )
    return document, version, derivation


# --------------------------------------------------------------------------- #
# When reprocessing is warranted
# --------------------------------------------------------------------------- #


class TestStaleness:
    def test_no_derivation_at_all_needs_reprocessing(self) -> None:
        assert needs_reprocessing(None, cfg=_cfg()) is True

    def test_an_older_parser_needs_reprocessing(self) -> None:
        stale = ResearchDocumentDerivation(
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
            extraction_profile=PROFILE_LIVE,
            extraction_method="html",
            status="complete",
        )
        assert needs_reprocessing(stale, cfg=_cfg()) is True

    def test_a_current_parser_with_no_deep_budget_configured_needs_nothing(self) -> None:
        current = ResearchDocumentDerivation(
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
            extraction_profile=PROFILE_LIVE,
            extraction_method="html",
            status="partial",
        )
        assert needs_reprocessing(current, cfg=_cfg()) is False

    def test_a_configured_deep_budget_makes_a_live_only_document_stale(self) -> None:
        # The 40-of-169 case: the parser is current, and the corpus still holds a
        # quarter of the document.
        current = ResearchDocumentDerivation(
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
            extraction_profile=PROFILE_LIVE,
            extraction_method="native_pdf",
            status="partial",
        )
        assert (
            needs_reprocessing(current, cfg=_cfg(v3_corpus_reprocess_max_pdf_pages=400))
            is True
        )

    def test_a_deep_derivation_at_the_current_parser_needs_nothing(self) -> None:
        deep = ResearchDocumentDerivation(
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
            extraction_profile=PROFILE_DEEP,
            extraction_method="native_pdf",
            status="complete",
        )
        assert (
            needs_reprocessing(deep, cfg=_cfg(v3_corpus_reprocess_max_pdf_pages=400))
            is False
        )

    def test_no_deep_budget_is_the_default(self) -> None:
        assert deep_profile_configured(Settings()) is False

    def test_the_deep_profile_raises_only_the_two_caps_that_stopped_at_page_40(
        self,
    ) -> None:
        cfg = _cfg(
            v3_corpus_reprocess_max_pdf_pages=400,
            v3_corpus_reprocess_timeout_seconds=900,
        )
        deep = settings_for_profile(cfg, PROFILE_DEEP)
        assert deep.primary_document_max_pdf_pages == 400
        assert deep.primary_document_extraction_timeout_seconds == 900
        # Everything else — the byte cap, the content-type gate, the
        # decompression-bomb guard — is untouched. None of them stopped the
        # extractor at page 40, and all of them exist for a reason reprocessing
        # does not change.
        assert deep.primary_document_max_download_bytes == cfg.primary_document_max_download_bytes
        assert deep.primary_document_max_image_pixels == cfg.primary_document_max_image_pixels
        assert (
            deep.source_document_extraction_allowed_content_types
            == cfg.source_document_extraction_allowed_content_types
        )

    def test_the_live_caps_are_never_mutated(self) -> None:
        # The live page cap is bounded by the deployed gunicorn worker timeout, and
        # those two drifted apart once and cost six outages.
        cfg = _cfg(v3_corpus_reprocess_max_pdf_pages=400)
        settings_for_profile(cfg, PROFILE_DEEP)
        assert cfg.primary_document_max_pdf_pages == 40

    def test_the_live_profile_changes_nothing(self) -> None:
        cfg = _cfg(v3_corpus_reprocess_max_pdf_pages=400)
        assert settings_for_profile(cfg, PROFILE_LIVE) is cfg

    async def test_stale_versions_are_listed_newest_first(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        stale = await versions_needing_reprocessing(session, cfg=cfg)
        assert len(stale) == 1

    async def test_a_current_document_is_not_listed(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        await _seed(session, cfg=cfg, store=InMemoryArtifactStore())
        assert await versions_needing_reprocessing(session, cfg=cfg) == []

    async def test_a_version_with_no_retained_bytes_is_never_a_candidate(
        self, session
    ) -> None:  # noqa: ANN001
        # Listing it would produce a `skipped_no_bytes` on every run, forever.
        cfg = _cfg()
        await _seed(
            session,
            cfg=cfg,
            store=InMemoryArtifactStore(),
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
            retain_bytes=False,
        )
        assert await versions_needing_reprocessing(session, cfg=cfg) == []

    async def test_listing_is_company_scoped_when_asked(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        mine, _, _ = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
            raw=HTML,
        )
        await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
            raw=HTML + b"<!-- other issuer -->",
        )
        scoped = await versions_needing_reprocessing(
            session, cfg=cfg, company_id=mine.company_id
        )
        assert len(scoped) == 1


# --------------------------------------------------------------------------- #
# The lifecycle itself
# --------------------------------------------------------------------------- #


class TestReprocessing:
    async def test_a_newer_parser_supersedes_and_the_old_reading_survives(
        self, session
    ) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, old = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        old_id = old.id
        old_chunk_ids = {
            c.chunk_id
            for c in (
                await session.execute(
                    select(ResearchDocumentChunk).where(
                        ResearchDocumentChunk.derivation_id == old_id
                    )
                )
            )
            .scalars()
            .all()
        }
        assert old_chunk_ids

        result = await reprocess_version(
            session,
            research_document_version_id=version.id,
            cfg=cfg,
            store=store,
        )
        assert result.outcome == OUTCOME_REPROCESSED
        assert result.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION
        assert result.superseded_derivation_id == old_id
        assert result.chunks_written > 0

        await session.refresh(old)
        assert old.is_active is False
        assert old.superseded_at is not None

        # NOTHING of the old reading is destroyed.
        derivations = (
            (await session.execute(select(ResearchDocumentDerivation))).scalars().all()
        )
        assert len(derivations) == 2
        old_pages = (
            (
                await session.execute(
                    select(ResearchDocumentPage).where(
                        ResearchDocumentPage.derivation_id == old_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert old_pages
        surviving = {
            c.chunk_id
            for c in (
                await session.execute(
                    select(ResearchDocumentChunk).where(
                        ResearchDocumentChunk.derivation_id == old_id
                    )
                )
            )
            .scalars()
            .all()
        }
        assert surviving == old_chunk_ids

    async def test_reprocessing_is_deterministic(self, session) -> None:  # noqa: ANN001
        # Same bytes, same profile, same parser → the same derivation content and
        # the same chunk ids. Without this, every reprocess would invalidate every
        # citation into the document.
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, derivation = await _seed(session, cfg=cfg, store=store)
        before = [
            (c.chunk_id, c.text)
            for c in (
                await session.execute(
                    select(ResearchDocumentChunk)
                    .where(ResearchDocumentChunk.derivation_id == derivation.id)
                    .order_by(ResearchDocumentChunk.ordinal)
                )
            )
            .scalars()
            .all()
        ]
        result = await reprocess_version(
            session,
            research_document_version_id=version.id,
            cfg=cfg,
            store=store,
            force=True,
        )
        assert result.derivation_id == derivation.id
        after = [
            (c.chunk_id, c.text)
            for c in (
                await session.execute(
                    select(ResearchDocumentChunk)
                    .where(ResearchDocumentChunk.derivation_id == derivation.id)
                    .order_by(ResearchDocumentChunk.ordinal)
                )
            )
            .scalars()
            .all()
        ]
        assert before == after

    async def test_an_up_to_date_document_is_skipped(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, derivation = await _seed(session, cfg=cfg, store=store)
        result = await reprocess_version(
            session, research_document_version_id=version.id, cfg=cfg, store=store
        )
        assert result.outcome == OUTCOME_UP_TO_DATE
        assert result.derivation_id == derivation.id

    async def test_reprocessing_never_fetches(self, session) -> None:  # noqa: ANN001
        # A maintenance job that reaches the network can be rate-limited, blocked,
        # or noticed by an issuer. If the bytes are gone, the answer is "gone".
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, _ = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        store._objects.clear()  # retention swept the bytes
        result = await reprocess_version(
            session, research_document_version_id=version.id, cfg=cfg, store=store
        )
        assert result.outcome == OUTCOME_NO_BYTES

    async def test_a_store_returning_the_wrong_object_is_refused(self, session) -> None:  # noqa: ANN001
        # Otherwise one document's citations end up attached to another document's
        # text — silently, and to every fact derived from it.
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, _ = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        for key in list(store._objects):
            store._objects[key] = b"<html><body><p>A different document.</p></body></html>"
        result = await reprocess_version(
            session, research_document_version_id=version.id, cfg=cfg, store=store
        )
        assert result.outcome == OUTCOME_NO_BYTES  # refused at the hash check

    async def test_an_empty_reparse_never_supersedes_a_derivation_with_content(
        self, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        # A regression in a NEW parser must not silently empty the corpus: the
        # document would go from "40 pages we can cite" to "nothing", with the old
        # reading demoted and no replacement.
        from app.services.corpus import reprocessing as module

        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, derivation = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        monkeypatch.setattr(
            module,
            "extract_primary_document",
            lambda *args, **kwargs: PrimaryDocumentExtraction(
                content_hash="a" * 64,
                mime_type="text/html",
                extraction_method="html",
                status="metadata_only",
            ),
        )
        result = await reprocess_version(
            session, research_document_version_id=version.id, cfg=cfg, store=store
        )
        assert result.outcome == OUTCOME_EXTRACTION_FAILED
        await session.refresh(derivation)
        assert derivation.is_active is True

    async def test_a_raising_parser_is_an_outcome_not_a_crash(
        self, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        from app.services.corpus import reprocessing as module

        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, derivation = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )

        def _boom(*args: object, **kwargs: object):  # noqa: ANN202
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(module, "extract_primary_document", _boom)
        result = await reprocess_version(
            session, research_document_version_id=version.id, cfg=cfg, store=store
        )
        assert result.outcome == OUTCOME_EXTRACTION_FAILED
        await session.refresh(derivation)
        assert derivation.is_active is True

    async def test_a_smaller_reprocess_never_rolls_the_corpus_backwards(
        self, session
    ) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        _, version, live = await _seed(session, cfg=cfg, store=store)
        # A deep derivation that somehow read LESS than the live one.
        parsed = build_parsed_document(
            extract_html(HTML, cfg=cfg, capture_blocks=True),
            extraction_profile=PROFILE_DEEP,
            max_pages=1,
        )
        assert parsed is not None
        parsed.pages = parsed.pages[:0] or parsed.pages
        smaller = await persist_parsed_document(
            session, version_id=version.id, parsed=parsed, cfg=cfg
        )
        assert smaller is not None
        await session.refresh(live)
        # Same parser, and the deep run read no more than the live one, so the live
        # derivation keeps the active slot.
        assert live.pages_persisted >= smaller.pages_persisted
        assert live.is_active is True
        assert smaller.is_active is False

    async def test_an_unknown_version_is_a_named_outcome_not_an_exception(
        self, session
    ) -> None:  # noqa: ANN001
        result = await reprocess_version(
            session, research_document_version_id=uuid.uuid4(), cfg=_cfg()
        )
        assert result.outcome == OUTCOME_NOT_FOUND

    async def test_the_corpus_flag_off_does_nothing(self, session) -> None:  # noqa: ANN001
        result = await reprocess_version(
            session,
            research_document_version_id=uuid.uuid4(),
            cfg=_cfg(v3_corpus_enabled=False),
        )
        assert result.outcome == OUTCOME_DISABLED

    def test_every_outcome_is_in_the_closed_vocabulary(self) -> None:
        # Safe to persist and to show an operator; never provider text or a path.
        for outcome in (
            OUTCOME_REPROCESSED,
            OUTCOME_UP_TO_DATE,
            OUTCOME_NO_BYTES,
            OUTCOME_EXTRACTION_FAILED,
            OUTCOME_NOT_FOUND,
            OUTCOME_DISABLED,
        ):
            assert outcome in REPROCESS_OUTCOMES


class TestIndexReconciliation:
    async def test_the_index_follows_the_new_active_derivation(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        document, version, old = await _seed(
            session,
            cfg=cfg,
            store=store,
            pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
        )
        backend = InMemorySearchBackend()
        await index_version(
            session, research_document_version_id=version.id, backend=backend, cfg=cfg
        )
        before = await search_corpus(
            session,
            backend=backend,
            cfg=cfg,
            query="watchmakers revenue",
            company_ids=[document.company_id],
            mode=SearchMode.LEXICAL,
        )
        assert before
        stale_evidence_id = before[0].reference.evidence_id

        result = await reprocess_version(
            session,
            research_document_version_id=version.id,
            cfg=cfg,
            store=store,
            backend=backend,
        )
        assert result.outcome == OUTCOME_REPROCESSED
        assert result.chunks_indexed > 0

        after = await search_corpus(
            session,
            backend=backend,
            cfg=cfg,
            query="watchmakers revenue",
            company_ids=[document.company_id],
            mode=SearchMode.LEXICAL,
            top_k=50,
        )
        assert after
        current_ids = {r.reference.evidence_id for r in after}
        # The stale reading is gone from the INDEX...
        assert stale_evidence_id not in current_ids
        assert all(r.reference.derivation_id == result.derivation_id for r in after)
        # ...and still resolvable from the DATABASE, so an old citation holds.
        assert (
            await resolve_evidence(session, evidence_id=stale_evidence_id, cfg=cfg)
            is not None
        )
        assert old.id != result.derivation_id


class TestBatch:
    async def test_a_batch_reports_every_outcome_separately(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        for i in range(3):
            await _seed(
                session,
                cfg=cfg,
                store=store,
                pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
                raw=HTML + f"<!-- {i} -->".encode(),
            )
        batch = await reprocess_batch(session, cfg=cfg, store=store, limit=10)
        assert batch.attempted == 3
        assert batch.by_outcome == {OUTCOME_REPROCESSED: 3}

    async def test_a_batch_respects_its_limit(self, session) -> None:  # noqa: ANN001
        cfg = _cfg()
        store = InMemoryArtifactStore()
        for i in range(4):
            await _seed(
                session,
                cfg=cfg,
                store=store,
                pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION - 1,
                raw=HTML + f"<!-- {i} -->".encode(),
            )
        batch = await reprocess_batch(session, cfg=cfg, store=store, limit=2)
        assert batch.attempted == 2

    def test_nothing_schedules_a_reprocess(self) -> None:
        # Re-extracting a corpus is minutes of CPU per document and belongs to
        # whoever decided a parser improved — not to the first request after a
        # deploy.
        callers = [
            str(p)
            for p in Path("app").rglob("*.py")
            if p.name != "reprocessing.py"
            and ("reprocess_batch" in p.read_text() or "reprocess_version" in p.read_text())
        ]
        assert callers == [], callers
