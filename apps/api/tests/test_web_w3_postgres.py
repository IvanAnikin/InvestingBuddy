"""Open-web W3 on real PostgreSQL — migration 044 and the web write path.

The static checks on the migration file always run. Everything touching a database is
skipped unless ``V3_TEST_POSTGRES_URL`` points at a PostgreSQL already at head (≥ 044).
SQLite runs with foreign keys off, so the lineage FKs, the partial unique index on
company-less document keys, ``BIGINT`` SimHash values and the PostgreSQL search
backend's web filters are only proven here. Every database test runs inside one
transaction that is rolled back: nothing is left behind.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "044_add_web_documents_to_corpus.py"
POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
FIXTURES = Path(__file__).parent / "fixtures" / "web"

requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head (>= 044)"
)


def _module():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("_m044", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_url() -> str:
    return POSTGRES_URL.replace("+asyncpg", "+psycopg")


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": POSTGRES_URL}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    failed = proc.returncode != 0  # output withheld: it can echo the database URL
    assert failed is False, f"alembic {' '.join(args)} failed (output withheld)"


class TestTheMigrationIsAdditive:
    def test_revision_and_temporary_parent(self) -> None:
        m = _module()
        # 042 on the W3 branch; re-pointed to 043 (report reconciliation) at merge.
        assert m.revision == "044" and m.down_revision in ("042", "043")
        assert "# re-pointed to 043 (report reconciliation) at merge" in MIGRATION.read_text()

    def test_upgrade_drops_and_alters_nothing(self) -> None:
        source = MIGRATION.read_text()
        upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]
        for forbidden in ("drop_", "alter_column", "rename"):
            assert forbidden not in upgrade, forbidden

    def test_every_new_column_is_nullable(self) -> None:
        source = MIGRATION.read_text()
        assert "op.add_column(table, sa.Column(name, type_, nullable=True))" in source

    def test_the_orm_and_the_migration_agree(self) -> None:
        from app.models.research_document import (
            ResearchDocument,
            ResearchDocumentSubject,
            ResearchDocumentVersion,
        )
        from app.models.research_lead import ResearchLeadRecord

        m = _module()
        added = {(t, c) for t, c, _type in m.COLUMNS}
        for model, names in (
            (ResearchDocument, ("subject_scope",)),
            (ResearchDocumentVersion, ("web_fetch_attempt_id", "use_constraint",
                                       "injection_suspect", "simhash", "origin_key",
                                       "published_at_source", "source_class",
                                       "web_extractor_version")),
            (ResearchLeadRecord, ("web_search_result_id", "research_document_version_id")),
        ):
            for name in names:
                assert name in model.__table__.columns, name
                assert (model.__tablename__, name) in added, name
        source = MIGRATION.read_text()
        for column in ResearchDocumentSubject.__table__.columns:
            assert f'"{column.name}"' in source, column.name


@requires_postgres
class TestMigrationOnRealPostgres:
    def test_upgrade_downgrade_upgrade_round_trip(self) -> None:
        def _columns(table: str) -> set[str]:
            engine = sa.create_engine(_sync_url())
            try:
                return {c["name"] for c in sa.inspect(engine).get_columns(table)}
            finally:
                engine.dispose()

        def _tables() -> set[str]:
            engine = sa.create_engine(_sync_url())
            try:
                return set(sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()

        _alembic("upgrade", "head")
        assert "research_document_subjects" in _tables()
        assert {"simhash", "use_constraint"} <= _columns("research_document_versions")
        _alembic("downgrade", _module().down_revision)
        assert "research_document_subjects" not in _tables()
        assert "simhash" not in _columns("research_document_versions")
        assert "research_document_version_id" not in _columns("research_leads")
        assert "subject_scope" not in _columns("research_documents")
        assert "web_fetch_attempts" in _tables()  # 042's tables untouched
        _alembic("upgrade", "head")
        assert "research_document_subjects" in _tables()

    def test_indexes_fks_and_the_partial_unique_index(self) -> None:
        engine = sa.create_engine(_sync_url())
        try:
            inspector = sa.inspect(engine)
            idx = {i["name"]: i for i in inspector.get_indexes("research_documents")}
            partial = idx["ix_research_documents_companyless_key"]
            assert partial["unique"] and partial["column_names"] == ["document_key"]
            where = (partial.get("dialect_options") or {}).get("postgresql_where")
            assert where is not None and "company_id IS NULL" in str(where)
            fks = {
                fk["name"]: fk["options"].get("ondelete")
                for fk in inspector.get_foreign_keys("research_document_versions")
            }
            assert fks["fk_research_document_versions_web_fetch_attempt_id"] == "SET NULL"
            lead_fks = {
                fk["name"]: fk["options"].get("ondelete")
                for fk in inspector.get_foreign_keys("research_leads")
            }
            assert lead_fks["fk_research_leads_web_search_result_id"] == "SET NULL"
            assert lead_fks["fk_research_leads_research_document_version_id"] == "SET NULL"
            subject_fks = {
                fk["referred_table"]: fk["options"].get("ondelete")
                for fk in inspector.get_foreign_keys("research_document_subjects")
            }
            assert subject_fks == {
                "research_documents": "CASCADE",
                "companies": "SET NULL",
                "legal_entities": "SET NULL",
            }
        finally:
            engine.dispose()


# --------------------------------------------------------------------------- #
# The write path on PostgreSQL
# --------------------------------------------------------------------------- #


def _cfg(**overrides: Any) -> Any:
    from app.core.config import Settings

    values: dict[str, Any] = {
        "v3_corpus_enabled": True,
        "v3_web_corpus_ingest_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def _fetched(body: bytes, url: str, *, content_class: str = "html",
             attempt_id: uuid.UUID | None = None) -> Any:
    from app.services.web_research.fetch import STATUS_FETCHED, OpenWebFetchResult

    return OpenWebFetchResult(
        status=STATUS_FETCHED, origin="search", requested_url=url, final_url=url,
        canonical_url=url, content=body, content_class=content_class, charset="utf-8",
        content_hash=hashlib.sha256(body).hexdigest(), bytes=len(body),
        tdm_decision="tdm_not_reserved", attempt_id=attempt_id,
    )


def _page(name: str, marker: str) -> bytes:
    raw = (FIXTURES / name).read_bytes().replace(b"{{TAG_CHARS}}", b"")
    # A per-test marker makes the bytes (and the URL key) unique in a shared database.
    return raw.replace(b"</body>", f"<p hidden>{marker}</p></body>".encode())


@pytest.fixture(scope="module")
def pool() -> Any:
    from app.services.web_research.pool import ExtractionPool

    p = ExtractionPool(1)
    yield p
    p.shutdown()


@pytest.fixture
async def pg() -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


async def _company(session: Any, name: str) -> uuid.UUID:
    from app.models.company import Company

    cid = uuid.uuid4()
    session.add(Company(id=cid, ticker=f"W3{uuid.uuid4().hex[:6]}".upper(), exchange="NYSE",
                        name=name))
    await session.flush()
    return cid


@requires_postgres
class TestWritePathOnPostgres:
    async def test_web_version_subjects_and_lineage(self, pg: Any, pool: Any) -> None:
        from app.models.research_document import (
            ResearchDocument,
            ResearchDocumentSubject,
            ResearchDocumentVersion,
        )
        from app.models.web_research import WebFetchAttempt
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend
        from app.services.web_research.entities import CandidateEntity
        from app.services.web_research.ingest import STATE_INGESTED, ingest_web_document

        marker = uuid.uuid4().hex
        hitachi = await _company(pg, "Hitachi Energy Ltd")
        siemens = await _company(pg, "Siemens Energy AG")
        prysmian = await _company(pg, "Prysmian S.p.A.")
        attempt = WebFetchAttempt(id=uuid.uuid4(), origin="search", status="fetched",
                                  requested_url=f"https://tdworld.com/{marker}")
        pg.add(attempt)
        await pg.flush()
        candidates = (
            CandidateEntity(name="Hitachi Energy Ltd", company_id=hitachi,
                            sector_terms=("Transformers",)),
            CandidateEntity(name="Siemens Energy AG", company_id=siemens,
                            tickers=(("ENR", "XETRA"),)),
            CandidateEntity(name="Prysmian S.p.A.", company_id=prysmian,
                            sector_terms=("Cables",)),
        )
        result = await ingest_web_document(
            pg, _fetched(_page("three_company_article.html", marker),
                         f"https://tdworld.com/{marker}", attempt_id=attempt.id),
            cfg=_cfg(), provider="tavily", subject_scope="industry", candidates=candidates,
            pool=pool, store=InMemoryArtifactStore(), backend=PostgresSearchBackend(pg),
        )
        assert result.state == STATE_INGESTED, result
        assert result.indexed > 0
        version = await pg.get(ResearchDocumentVersion, result.version_id)
        assert version.web_fetch_attempt_id == attempt.id
        assert version.simhash is not None and -(2**63) <= version.simhash < 2**63
        assert version.source_class == "trade_publication" and version.use_constraint == "unknown"
        rows = (await pg.execute(
            sa.select(ResearchDocumentSubject).where(
                ResearchDocumentSubject.research_document_id == result.document_id)
        )).scalars().all()
        assert {r.company_id for r in rows} == {hitachi, siemens, prysmian}
        document = await pg.get(ResearchDocument, result.document_id)
        assert document.company_id is None and document.subject_scope == "industry"

        # ON DELETE SET NULL: deleting the fetch attempt never deletes the evidence.
        await pg.delete(attempt)
        await pg.flush()
        await pg.refresh(version)
        assert version.web_fetch_attempt_id is None

    async def test_companyless_key_is_unique_in_the_database(self, pg: Any, pool: Any) -> None:
        from sqlalchemy.exc import IntegrityError

        from app.models.research_document import ResearchDocument
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.web_research.ingest import ingest_web_document

        marker = uuid.uuid4().hex
        url = f"https://international-aluminium.org/{marker}"
        body = _page("association_page.html", marker)
        store = InMemoryArtifactStore()
        r1 = await ingest_web_document(pg, _fetched(body, url), cfg=_cfg(),
                                       subject_scope="theme", theme_key="theme:aluminium",
                                       pool=pool, store=store)
        r2 = await ingest_web_document(pg, _fetched(body.replace(b"latest", b"newest"), url),
                                       cfg=_cfg(), subject_scope="theme",
                                       theme_key="theme:aluminium", pool=pool, store=store)
        assert r1.document_id == r2.document_id and r1.version_id != r2.version_id
        document = await pg.get(ResearchDocument, r1.document_id)
        now = datetime.now(timezone.utc)
        with pytest.raises(IntegrityError):
            async with pg.begin_nested():
                pg.add(ResearchDocument(id=uuid.uuid4(), company_id=None,
                                        document_key=document.document_key,
                                        document_type="web_page",
                                        first_seen_at=now, last_seen_at=now))
                await pg.flush()

    async def test_ev_x_resolves_through_the_lead_fk(self, pg: Any, pool: Any) -> None:
        from app.models.research_lead import ResearchLeadRecord
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.web_research.ingest import (
            ingest_web_document,
            version_for_external_evidence,
        )

        marker = uuid.uuid4().hex
        company = await _company(pg, "Grid Holdings")
        body = _page("news_article.html", marker)
        result = await ingest_web_document(
            pg, _fetched(body, f"https://www.gridweekly.example/{marker}"), cfg=_cfg(),
            company_id=company, pool=pool, store=InMemoryArtifactStore(),
        )
        evidence_id = f"ev:x:{marker[:24]}"
        pg.add(ResearchLeadRecord(
            id=uuid.uuid4(), company_id=company, provider="tavily", lead_key=marker,
            slot_key=marker, claim_text="Lead times stretched", status="verified",
            fetched_content_hash=hashlib.sha256(body).hexdigest(),
            promoted_evidence_id=evidence_id,
            research_document_version_id=result.version_id,
        ))
        await pg.flush()
        version = await version_for_external_evidence(pg, evidence_id)
        assert version is not None and version.id == result.version_id


@requires_postgres
class TestPostgresFilters:
    async def test_web_filters_apply_inside_the_search(self, pg: Any, pool: Any) -> None:
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend
        from app.services.corpus.search.types import CorpusFilters, CorpusQuery, SearchMode
        from app.services.web_research.ingest import ingest_web_document

        marker = uuid.uuid4().hex
        company = await _company(pg, "Filter Test Co")
        backend = PostgresSearchBackend(pg)
        store = InMemoryArtifactStore()
        press = await ingest_web_document(
            pg, _fetched(_page("news_article.html", marker), f"https://www.reuters.com/{marker}"),
            cfg=_cfg(), company_id=company, pool=pool, store=store, backend=backend)
        suspect = await ingest_web_document(
            pg, _fetched(_page("injection_page.html", marker), f"https://blog.example/{marker}"),
            cfg=_cfg(), company_id=company, pool=pool, store=store, backend=backend)
        theme = await ingest_web_document(
            pg, _fetched(_page("government_page.html", marker), f"https://www.usgs.gov/{marker}"),
            cfg=_cfg(), subject_scope="theme", theme_key=f"theme:{marker}", pool=pool,
            store=store, backend=backend)
        assert suspect.injection_suspect

        async def versions(**filters: Any) -> set[uuid.UUID]:
            query = CorpusQuery(text="copper transformer lead times demand",
                                filters=CorpusFilters(**filters), mode=SearchMode.LEXICAL,
                                top_k=100)
            return {h.chunk.research_document_version_id for h in await backend.search(query)}

        mine = {press.version_id, suspect.version_id}
        assert await versions(company_ids=(company,)) == mine
        assert await versions(company_ids=(company,), exclude_injection_suspect=True) == {
            press.version_id
        }
        assert await versions(company_ids=(company,),
                              source_classes=("major_financial_press",)) == {press.version_id}
        assert await versions(theme_keys=(f"theme:{marker}",)) == {theme.version_id}
        assert await versions(theme_keys=(f"theme:{marker}",),
                              use_constraints=("public_domain",)) == {theme.version_id}
        assert await versions(theme_keys=(f"theme:{marker}",),
                              use_constraints=("unknown",)) == set()
        assert await versions(company_ids=(company,), subject_scopes=("theme",)) == set()


# --------------------------------------------------------------------------- #
# Review round 1 (W3)
# --------------------------------------------------------------------------- #


@requires_postgres
class TestReviewOnPostgres:
    async def test_subject_identity_is_unique_with_nulls(self, pg: Any, pool: Any) -> None:
        # F10: the coalescing unique index — two "no company" theme rows collide.
        from sqlalchemy.exc import IntegrityError

        from app.models.research_document import ResearchDocumentSubject
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.web_research.ingest import ingest_web_document, write_subjects

        marker = uuid.uuid4().hex
        result = await ingest_web_document(
            pg, _fetched(_page("association_page.html", marker),
                         f"https://international-aluminium.org/{marker}"),
            cfg=_cfg(), subject_scope="theme", theme_key=f"theme:{marker}", pool=pool,
            store=InMemoryArtifactStore())
        again = await write_subjects(pg, document_id=result.document_id,
                                     version_id=result.version_id, company_id=None,
                                     mentions=[], theme_key=f"theme:{marker}")
        assert again == 0  # ON CONFLICT DO NOTHING
        with pytest.raises(IntegrityError):
            async with pg.begin_nested():
                pg.add(ResearchDocumentSubject(
                    id=uuid.uuid4(), research_document_id=result.document_id,
                    relation="theme", method="research_run", theme_key=f"theme:{marker}"))
                await pg.flush()

    async def test_memory_and_postgres_agree_after_reuse(self, pg: Any, pool: Any) -> None:
        # F4 + F2 + F3: after a second theme and a company run REUSE the stored bytes,
        # both backends return the same chunks with the same subject marking.
        from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
        from app.services.corpus.search.backends.memory import InMemorySearchBackend
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend
        from app.services.corpus.search.types import CorpusFilters, CorpusQuery, SearchMode
        from app.services.web_research.entities import CandidateEntity
        from app.services.web_research.ingest import STATE_REUSED, ingest_web_document

        marker = uuid.uuid4().hex
        hitachi = await _company(pg, "Hitachi Energy Ltd")
        siemens = await _company(pg, "Siemens Energy AG")
        run_company = await _company(pg, "Run Company")
        memory = InMemorySearchBackend()
        store = InMemoryArtifactStore()
        body = _page("three_company_article.html", marker)
        url = f"https://tdworld.com/{marker}"
        candidates = (
            CandidateEntity(name="Hitachi Energy Ltd", company_id=hitachi,
                            sector_terms=("Transformers",)),
            CandidateEntity(name="Siemens Energy AG", company_id=siemens,
                            tickers=(("ENR", "XETRA"),)),
        )
        first = await ingest_web_document(
            pg, _fetched(body, url), cfg=_cfg(), subject_scope="industry",
            theme_key=f"theme:a{marker}", candidates=candidates, pool=pool, store=store,
            backend=memory)
        second = await ingest_web_document(
            pg, _fetched(body, url), cfg=_cfg(), subject_scope="industry",
            theme_key=f"theme:b{marker}", pool=pool, store=store, backend=memory)
        third = await ingest_web_document(
            pg, _fetched(body, url), cfg=_cfg(), company_id=run_company, pool=pool,
            store=store, backend=memory)
        assert second.state == third.state == STATE_REUSED
        assert first.version_id == second.version_id == third.version_id
        postgres = PostgresSearchBackend(pg)
        from app.services.corpus.indexing import index_version

        await index_version(pg, research_document_version_id=first.version_id,
                            backend=postgres, cfg=_cfg())

        async def view(backend: Any, **filters: Any) -> set[tuple[str, bool, str | None]]:
            query = CorpusQuery(text="transformer cables order backlog", top_k=50,
                                mode=SearchMode.LEXICAL, filters=CorpusFilters(**filters),
                                allow_cross_entity=True)
            return {(h.chunk.chunk_id, h.chunk.via_subject, h.chunk.scope_type)
                    for h in await backend.search(query)}

        cases = [
            {"theme_keys": (f"theme:b{marker}",)},
            {"subject_company_ids": (hitachi,)},
            {"subject_company_ids": (siemens,)},
            {"subject_company_ids": (run_company,)},
            {"company_ids": (hitachi,), "subject_company_ids": (hitachi,),
             "scope_types": ("mention",)},
            {"company_ids": (hitachi,), "subject_company_ids": (hitachi,),
             "scope_types": ("group",)},
        ]
        for filters in cases:
            assert await view(memory, **filters) == await view(postgres, **filters), filters
        assert len(await view(postgres, subject_company_ids=(hitachi,))) == 1
        assert await view(postgres, theme_keys=(f"theme:b{marker}",))

    async def test_a_failed_lead_store_keeps_the_outer_transaction_and_the_lead(
        self, pg: Any, pool: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # F6: an FK violation inside the store phase costs the corpus link only.
        from app.models.research_document import ResearchDocumentVersion
        from app.models.research_lead import ResearchLeadRecord
        from app.services.agent_tools.external import _fetch_public_source
        from app.services.agent_tools.session import ToolContext
        from app.services.providers import leads as leads_mod
        from app.services.web_research import fetch as fetch_mod
        from app.services.web_research import ingest as ingest_mod
        from app.services.web_research import pool as pool_mod

        marker = uuid.uuid4().hex
        company = await _company(pg, "Grid Holdings")
        body = _page("news_article.html", marker)
        url = f"https://www.gridweekly.example/{marker}"

        class _Result:
            content, final_url, document_type, blocked = body, url, "html", False
            ok, error, status_class, truncated, headers = True, None, "2xx", False, {}

        async def _fetcher(*_a: Any, **_kw: Any) -> Any:
            return _Result()

        async def _clearance(*_a: Any, **_kw: Any) -> Any:
            return fetch_mod.IngestionClearance(True, "allowed", "tdm_not_reserved")

        real_write = ingest_mod.write_subjects

        async def _broken_write(session: Any, **kw: Any) -> int:
            from app.models.research_document import ResearchDocumentSubject

            session.add(ResearchDocumentSubject(
                id=uuid.uuid4(), research_document_id=kw["document_id"],
                company_id=uuid.uuid4(),  # no such company → FK violation at flush
                relation="mentioned", method="test"))
            await session.flush()
            return await real_write(session, **kw)

        monkeypatch.setattr(leads_mod, "_default_fetcher", _fetcher)
        monkeypatch.setattr(fetch_mod, "ingestion_clearance", _clearance)
        monkeypatch.setattr(pool_mod, "get_extraction_pool", lambda *_a, **_k: pool)
        monkeypatch.setattr(ingest_mod, "write_subjects", _broken_write)
        context = ToolContext(session=pg, cfg=_cfg(v3_web_fetch_enabled=True),
                              company_id=company)
        payload = await _fetch_public_source(context, {
            "url": url,
            "claim": "Lead times for large power transformers have stretched to between "
                     "120 and 210 weeks",
            "provider": "tavily",
        })
        record = payload["items"][0]
        assert record["verified"] and record["corpus_version_id"] is None
        lead = (await pg.execute(sa.select(ResearchLeadRecord).where(
            ResearchLeadRecord.company_id == company))).scalar_one()
        assert lead.research_document_version_id is None
        # The outer transaction is still usable.
        count = (await pg.execute(sa.select(sa.func.count()).select_from(
            ResearchDocumentVersion).where(
            ResearchDocumentVersion.content_hash == lead.fetched_content_hash))).scalar_one()
        assert count == 0
