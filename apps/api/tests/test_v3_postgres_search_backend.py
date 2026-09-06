"""V3.4 Slice 4.9 — the PostgreSQL corpus search backend.

TWO TEST POPULATIONS, ON PURPOSE
================================
The parts that are pure SQLAlchemy — index state, de-indexing, embedding attribution,
filter parity, the factory — run on the sqlite database every other unit test uses.

The parts that are **PostgreSQL** — ``to_tsvector``, ``ts_rank_cd``, the GIN expression
index — run against a real PostgreSQL 16 when one is reachable, and skip with a named
reason when it is not. A backend whose only successful path is a unit test is the
failure this repository already recorded once, in Phase 32A slice 5A: "0 successful
native extractions across 7 live issuers; success path only in unit tests."
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.research_chunk import ResearchDocumentChunk
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.backends.postgres import (
    PostgresSearchBackend,
    to_tsquery_string,
)
from app.services.corpus.search.embeddings import (
    DeterministicEmbeddingProvider,
    cosine,
)
from app.services.corpus.search.factory import (
    UnknownSearchBackendError,
    get_search_backend,
)
from app.services.corpus.search.types import (
    CorpusChunk,
    CorpusFilters,
    CorpusQuery,
    SearchMode,
    VectorOnlyRetrievalError,
)

T0 = datetime(2026, 9, 6, tzinfo=timezone.utc)

#: The local development PostgreSQL. Overridable, and never a remote database: these
#: tests CREATE and DROP a scratch schema.
PG_URL = os.environ.get(
    "V3_SEARCH_PG_URL",
    "postgresql+psycopg://investingbuddy:investingbuddy@localhost:5432/postgres",
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _postgres_available() -> tuple[bool, str]:
    try:
        import psycopg
    except Exception as exc:  # noqa: BLE001
        return False, f"psycopg unavailable: {type(exc).__name__}"
    dsn = PG_URL.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"no PostgreSQL at {dsn.split('@')[-1]}: {type(exc).__name__}"


_PG_OK, _PG_WHY = _postgres_available()
requires_postgres = pytest.mark.skipif(not _PG_OK, reason=_PG_WHY or "no PostgreSQL")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestQueryConstruction:
    def test_tokens_become_or_joined_quoted_lexemes(self) -> None:
        assert to_tsquery_string("group revenue") == "'group' | 'revenue'"

    def test_an_identifier_survives_as_one_term(self) -> None:
        # A financial corpus is full of tokens a word-only tokeniser destroys, and an
        # unquoted `mrna-1273` is a tsquery expression rather than a search term.
        assert to_tsquery_string("mRNA-1273") == "'mrna-1273'"

    def test_a_query_of_only_punctuation_produces_nothing(self) -> None:
        assert to_tsquery_string("!!! ???") == ""

    def test_tsquery_operators_cannot_be_smuggled_in(self) -> None:
        # The tokeniser's character class already excludes every tsquery operator, so
        # the constructed string cannot carry one. Belt and braces; the value is also
        # passed as a bind parameter.
        built = to_tsquery_string("revenue & (secret | other) <-> x:*")
        assert "&" not in built and "<->" not in built and ":*" not in built


class TestEmbeddings:
    def test_the_fake_provider_is_deterministic(self) -> None:
        provider = DeterministicEmbeddingProvider()
        assert provider.embed_one("group revenue") == provider.embed_one("group revenue")

    def test_similar_text_is_closer_than_unrelated_text(self) -> None:
        provider = DeterministicEmbeddingProvider()
        a = provider.embed_one("group revenue increased in fy2025")
        b = provider.embed_one("revenue for the group grew during fy2025")
        c = provider.embed_one("clinical trial phase three readout")
        assert cosine(a, b) > cosine(a, c)

    def test_it_says_it_is_fake(self) -> None:
        # Not a naming convention: a deployed environment may refuse to enable a
        # semantic leg on the strength of this field.
        assert DeterministicEmbeddingProvider().is_fake is True

    def test_two_models_vectors_are_never_compared(self) -> None:
        small = DeterministicEmbeddingProvider(dimension=8, model_id="a")
        large = DeterministicEmbeddingProvider(dimension=64, model_id="b")
        assert cosine(small.embed_one("x"), large.embed_one("x")) == 0.0


class TestFactory:
    def test_the_default_is_memory(self) -> None:
        assert get_search_backend(Settings()).name == "memory"

    def test_postgres_is_selected_by_configuration(self) -> None:
        backend = get_search_backend(
            Settings(v3_search_backend="postgres"), session=object()
        )
        assert backend.name == "postgres"

    def test_an_unknown_name_raises_rather_than_falling_back(self) -> None:
        with pytest.raises(UnknownSearchBackendError):
            get_search_backend(Settings(v3_search_backend="azure_ai_search"))

    def test_postgres_without_a_session_raises(self) -> None:
        with pytest.raises(UnknownSearchBackendError, match="database session"):
            get_search_backend(Settings(v3_search_backend="postgres"))

    def test_the_backend_does_not_declare_semantic_only_retrieval(self) -> None:
        backend = get_search_backend(
            Settings(v3_search_backend="postgres"), session=object()
        )
        assert SearchMode.SEMANTIC not in backend.capabilities
        assert backend.semantic_is_exhaustive is False


# --------------------------------------------------------------------------- #
# Index state, on sqlite
# --------------------------------------------------------------------------- #


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


def _stable_ordinal(chunk_id: str) -> int:
    import hashlib

    return int.from_bytes(hashlib.sha256(chunk_id.encode()).digest()[:4], "big") % 100_000


async def _corpus_chain(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A real document -> version -> derivation chain.

    PostgreSQL enforces the foreign keys sqlite's default configuration does not, which
    is itself the argument for running these tests against the real thing: a chunk with
    no derivation is a chunk with no lineage, and the schema is supposed to say so.
    """
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import (
        ResearchDocument,
        ResearchDocumentVersion,
    )

    document = ResearchDocument(
        id=uuid.uuid4(),
        document_key=f"doc:{uuid.uuid4().hex[:12]}",
        document_type="annual_report",
        first_seen_at=T0,
        last_seen_at=T0,
    )
    session.add(document)
    # Flushed one at a time: `research_documents` and `research_document_versions`
    # reference each other, so SQLAlchemy's unit of work has a cycle to break and its
    # insert order across the three tables is not guaranteed. PostgreSQL enforces the
    # keys sqlite's default configuration does not, which is how this surfaced at all.
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        canonical_url="https://issuer.example/annual-2025.pdf",
        transport="company_ir",
        source_tier="T1_primary_filing",
        access_class="public_issuer",
        retrieved_at=T0,
        extraction_status="extracted",
        is_current=True,
    )
    session.add(version)
    await session.flush()
    derivation = ResearchDocumentDerivation(
        id=uuid.uuid4(),
        research_document_version_id=version.id,
        pipeline_version=15,
        extraction_method="pdf_text",
        extraction_profile="live",
        status="complete",
        is_active=True,
        pages_persisted=1,
        paginated=True,
        char_count=100,
        section_count=1,
        table_count=0,
        truncated=False,
    )
    session.add(derivation)
    await session.flush()
    return version.id, derivation.id


async def _seed_chunk(
    session: Any,
    *,
    chunk_id: str,
    text: str = "Group revenue was 31,338 million DKK in FY2025.",
    company_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    derivation_id: uuid.UUID | None = None,
    indexable: bool = True,
    period_key: str | None = "2025",
    scope_key: str | None = "group",
    ordinal: int | None = None,
) -> ResearchDocumentChunk:
    row = ResearchDocumentChunk(
        id=uuid.uuid4(),
        chunk_id=chunk_id,
        derivation_id=derivation_id or uuid.uuid4(),
        research_document_version_id=version_id or uuid.uuid4(),
        company_id=company_id,
        kind="prose",
        # `(derivation_id, ordinal)` is UNIQUE — document order within a derivation is
        # a real constraint, and PostgreSQL enforces it. Derived from the chunk id with
        # a STABLE digest, not `hash()`: Python randomises string hashing per process,
        # so a test built on it would be a flake with a schedule.
        ordinal=ordinal if ordinal is not None else _stable_ordinal(chunk_id),
        text=text,
        char_start=0,
        char_end=len(text),
        page_start=1,
        page_end=1,
        period_key=period_key,
        period_type="annual",
        scope_type="group" if scope_key == "group" else "segment",
        scope_key=scope_key,
        document_type="annual_report",
        source_tier="T1_primary_filing",
        access_class="public_issuer",
        language="en",
        published_at=date(2026, 2, 1),
        indexable=indexable,
    )
    session.add(row)
    await session.flush()
    return row


def _corpus_chunk(row: ResearchDocumentChunk, **overrides: Any) -> CorpusChunk:
    base: dict[str, Any] = {
        "chunk_id": row.chunk_id,
        "text": row.text,
        "research_document_version_id": row.research_document_version_id,
        "company_id": row.company_id,
        "indexable": row.indexable,
    }
    base.update(overrides)
    return CorpusChunk(**base)


class TestIndexState:
    async def test_indexing_marks_a_row_rather_than_copying_the_corpus(
        self, session
    ) -> None:
        row = await _seed_chunk(session, chunk_id="c1")
        backend = PostgresSearchBackend(session)
        result = await backend.index([_corpus_chunk(row)])
        assert result.indexed == 1
        assert result.updated == 0
        await session.refresh(row)
        assert row.indexed_at is not None

    async def test_reindexing_updates_rather_than_duplicating(self, session) -> None:
        row = await _seed_chunk(session, chunk_id="c1")
        backend = PostgresSearchBackend(session)
        await backend.index([_corpus_chunk(row)])
        result = await backend.index([_corpus_chunk(row)])
        assert result.indexed == 0
        assert result.updated == 1

    async def test_a_chunk_governance_forbids_indexing_is_skipped(
        self, session
    ) -> None:
        row = await _seed_chunk(session, chunk_id="c1", indexable=False)
        backend = PostgresSearchBackend(session)
        result = await backend.index([_corpus_chunk(row)])
        assert result.skipped_not_indexable == 1
        await session.refresh(row)
        # Checked here as well as in the query filter: a row that is in the index and
        # filtered out at read time is one forgotten WHERE clause from being returned.
        assert row.indexed_at is None

    async def test_a_chunk_with_no_corpus_row_is_never_invented(self, session) -> None:
        backend = PostgresSearchBackend(session)
        result = await backend.index(
            [CorpusChunk(chunk_id="never-persisted", text="x")]
        )
        assert result.indexed == 0
        assert result.skipped_not_indexable == 1
        assert (await session.execute(select(ResearchDocumentChunk))).first() is None

    async def test_deindexing_is_an_update_not_a_delete(self, session) -> None:
        """The rows ARE the corpus. Deleting them destroys spans citations resolve to."""
        version_id = uuid.uuid4()
        row = await _seed_chunk(session, chunk_id="c1", version_id=version_id)
        backend = PostgresSearchBackend(session)
        await backend.index([_corpus_chunk(row)])
        removed = await backend.delete(research_document_version_id=version_id)
        assert removed == 1
        await session.refresh(row)
        assert row.indexed_at is None
        # And the span is still there to be cited.
        assert (
            await session.execute(select(ResearchDocumentChunk))
        ).scalars().first() is not None

    async def test_deindexing_a_version_that_was_never_indexed_removes_nothing(
        self, session
    ) -> None:
        version_id = uuid.uuid4()
        await _seed_chunk(session, chunk_id="c1", version_id=version_id)
        backend = PostgresSearchBackend(session)
        assert await backend.delete(research_document_version_id=version_id) == 0

    async def test_an_embedding_cannot_be_stored_without_its_model(
        self, session
    ) -> None:
        row = await _seed_chunk(session, chunk_id="c1")
        backend = PostgresSearchBackend(session)  # no embedding_model configured
        with pytest.raises(ValueError, match="model that produced it"):
            await backend.index([_corpus_chunk(row, embedding=(0.1, 0.2))])

    async def test_an_embedding_is_stored_with_its_model_and_dimension(
        self, session
    ) -> None:
        row = await _seed_chunk(session, chunk_id="c1")
        backend = PostgresSearchBackend(session, embedding_model="fake-hash-v1")
        await backend.index([_corpus_chunk(row, embedding=(0.1, 0.2, 0.3))])
        await session.refresh(row)
        assert row.embedding_model == "fake-hash-v1"
        assert row.embedding_dim == 3

    async def test_an_unattributed_embedding_is_unstorable(self, session) -> None:
        """The CHECK, exercised. A vector nobody can attribute compares to nothing."""
        row = await _seed_chunk(session, chunk_id="c1")
        row.embedding_json = [0.1, 0.2]
        row.embedding_model = None
        row.embedding_dim = None
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


class TestFilterParity:
    """The SQL predicates and ``CorpusFilters.matches`` must agree.

    A filter implemented in one backend and forgotten in another is how "FY2025 only"
    quietly starts meaning something else when the backend changes. Both are evaluated
    over the same rows and compared.
    """

    async def test_every_filter_dimension_selects_the_same_rows(self, session) -> None:
        from app.services.corpus.search.backends.postgres import _filter_clauses

        company_a, company_b = uuid.uuid4(), uuid.uuid4()
        session.add_all(
            [
                Company(id=company_a, ticker="CFR", exchange="SW", name="A", status="new"),
                Company(id=company_b, ticker="MC", exchange="PA", name="B", status="new"),
            ]
        )
        await session.flush()
        rows = [
            await _seed_chunk(
                session, chunk_id="a-2025", company_id=company_a, period_key="2025"
            ),
            await _seed_chunk(
                session, chunk_id="a-2024", company_id=company_a, period_key="2024"
            ),
            await _seed_chunk(
                session, chunk_id="b-2025", company_id=company_b, period_key="2025"
            ),
            await _seed_chunk(
                session,
                chunk_id="a-seg",
                company_id=company_a,
                period_key="2025",
                scope_key="segment:watches",
            ),
            await _seed_chunk(
                session, chunk_id="a-private", company_id=company_a, indexable=False
            ),
        ]
        corpus_chunks = {r.chunk_id: _row_as_corpus(r) for r in rows}

        cases = [
            CorpusFilters(company_ids=(company_a,)),
            CorpusFilters(company_ids=(company_a,), period_keys=("2025",)),
            CorpusFilters(company_ids=(company_a,), scope_keys=("group",)),
            CorpusFilters(company_ids=(company_a, company_b), period_types=("annual",)),
            CorpusFilters(company_ids=(company_a,), languages=("fr",)),
            CorpusFilters(
                company_ids=(company_a,), published_from=date(2026, 3, 1)
            ),
            CorpusFilters(company_ids=(company_a,), published_to=date(2026, 3, 1)),
            CorpusFilters(
                company_ids=(company_a,), source_tiers=("T1_primary_filing",)
            ),
            CorpusFilters(company_ids=(company_a,), access_classes=("user_private",)),
            CorpusFilters(company_ids=(company_a,), document_types=("transcript",)),
        ]
        for filters in cases:
            sql_ids = {
                r.chunk_id
                for r in (
                    await session.execute(
                        select(ResearchDocumentChunk).where(*_filter_clauses(filters))
                    )
                )
                .scalars()
                .all()
            }
            python_ids = {
                cid
                for cid, chunk in corpus_chunks.items()
                if filters.matches(chunk)
            }
            assert sql_ids == python_ids, f"parity broke for {filters}"


def _row_as_corpus(row: ResearchDocumentChunk) -> CorpusChunk:
    return CorpusChunk(
        chunk_id=row.chunk_id,
        text=row.text,
        company_id=row.company_id,
        document_type=row.document_type,
        source_tier=row.source_tier,
        access_class=row.access_class,
        period_key=row.period_key,
        period_type=row.period_type,
        scope_type=row.scope_type,
        scope_key=row.scope_key,
        language=row.language,
        published_at=row.published_at,
        indexable=row.indexable,
    )


# --------------------------------------------------------------------------- #
# Real PostgreSQL
# --------------------------------------------------------------------------- #


@pytest.fixture
async def pg_chain(pg_session):  # noqa: ANN201
    """A session plus the version/derivation ids every chunk in it must reference."""
    version_id, derivation_id = await _corpus_chain(pg_session)
    return pg_session, version_id, derivation_id


@pytest.fixture
async def pg_session():  # noqa: ANN201
    """A scratch PostgreSQL schema, created and dropped per test module run."""
    schema = f"v3_search_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    await admin.dispose()

    engine = create_async_engine(
        PG_URL, connect_args={"options": f"-csearch_path={schema}"}
    )
    async with engine.begin() as conn:
        # The FTS index comes from the ORM definition, which is also what migration 032
        # creates — so this fixture exercises the real thing rather than a hand-written
        # copy that could drift from it.
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()

    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    await admin.dispose()


@requires_postgres
class TestRealPostgresSearch:
    async def test_a_lexical_query_returns_the_right_chunk(self, pg_session) -> None:
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="PNDORA", exchange="CO", name="P", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        rows = [
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="cash",
                company_id=company,
                text="Cash flow from operations was 6,900 million DKK.",
            ),
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="board",
                company_id=company,
                text="The board of directors proposes a dividend per share.",
            ),
        ]
        backend = PostgresSearchBackend(pg_session)
        await backend.index([_corpus_chunk(r) for r in rows])
        hits = await backend.search(
            CorpusQuery(
                text="cash flow from operations",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.LEXICAL,
            )
        )
        assert [h.chunk.chunk_id for h in hits][:1] == ["cash"]
        assert hits[0].lexical_score is not None
        assert hits[0].semantic_score is None
        assert "cash" in hits[0].matched_terms

    async def test_an_unindexed_chunk_is_never_returned(self, pg_session) -> None:
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        await _seed_chunk(
            pg_session, version_id=version_id, derivation_id=derivation_id, chunk_id="unindexed", company_id=company, text="revenue grew"
        )
        backend = PostgresSearchBackend(pg_session)
        hits = await backend.search(
            CorpusQuery(
                text="revenue",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.LEXICAL,
            )
        )
        assert hits == []

    async def test_the_period_filter_applies_inside_the_search(
        self, pg_session
    ) -> None:
        """The property the whole backend exists to have.

        A retrieval that ranks first and filters afterwards returns the top matches from
        the WRONG periods; the right ones were never retrieved. Here the FY2024 chunk is
        the better textual match and must still not be returned.
        """
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="CFR", exchange="SW", name="R", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        rows = [
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="fy2024",
                company_id=company,
                period_key="2024",
                text="Group revenue Group revenue Group revenue rose in the year.",
            ),
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="fy2025",
                company_id=company,
                period_key="2025",
                text="Group revenue was higher.",
            ),
        ]
        backend = PostgresSearchBackend(pg_session)
        await backend.index([_corpus_chunk(r) for r in rows])
        hits = await backend.search(
            CorpusQuery(
                text="group revenue",
                filters=CorpusFilters(company_ids=(company,), period_keys=("2025",)),
                mode=SearchMode.LEXICAL,
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["fy2025"]

    async def test_a_segment_chunk_is_not_returned_for_a_group_query(
        self, pg_session
    ) -> None:
        """The CFR failure mode, as a retrieval filter."""
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="CFR", exchange="SW", name="R", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        rows = [
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="segment",
                company_id=company,
                scope_key="segment:specialist watchmakers",
                text="Sales were 107 million euro.",
            ),
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id,
                chunk_id="group",
                company_id=company,
                scope_key="group",
                text="Sales were 21,400 million euro.",
            ),
        ]
        backend = PostgresSearchBackend(pg_session)
        await backend.index([_corpus_chunk(r) for r in rows])
        hits = await backend.search(
            CorpusQuery(
                text="sales",
                filters=CorpusFilters(company_ids=(company,), scope_keys=("group",)),
                mode=SearchMode.LEXICAL,
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["group"]

    async def test_cross_entity_search_must_be_asked_for(self, pg_session) -> None:
        backend = PostgresSearchBackend(pg_session)
        from app.services.corpus.search.types import UnscopedRetrievalError

        with pytest.raises(UnscopedRetrievalError):
            await backend.search(CorpusQuery(text="revenue"))

    async def test_semantic_only_retrieval_is_refused(self, pg_session) -> None:
        backend = PostgresSearchBackend(pg_session)
        with pytest.raises(VectorOnlyRetrievalError):
            await backend.search(
                CorpusQuery(
                    text="revenue",
                    mode=SearchMode.SEMANTIC,
                    allow_cross_entity=True,
                )
            )

    async def test_hybrid_degrades_to_lexical_when_no_embedding_exists(
        self, pg_session
    ) -> None:
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        row = await _seed_chunk(
            pg_session, version_id=version_id, derivation_id=derivation_id, chunk_id="c1", company_id=company, text="revenue grew strongly"
        )
        backend = PostgresSearchBackend(pg_session, semantic_enabled=True)
        await backend.index([_corpus_chunk(row)])
        hits = await backend.search(
            CorpusQuery(
                text="revenue",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.HYBRID,
                embedding=DeterministicEmbeddingProvider().embed_one("revenue"),
            )
        )
        # A worse answer, never a wrong one — and the result says which leg ran.
        assert [h.chunk.chunk_id for h in hits] == ["c1"]
        assert hits[0].semantic_score is None

    async def test_the_semantic_leg_runs_with_deterministic_embeddings(
        self, pg_session
    ) -> None:
        provider = DeterministicEmbeddingProvider()
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        texts = {
            "ops": "Cash flow from operations improved during the period.",
            "div": "Cash returned to shareholders through dividends and buybacks.",
        }
        rows = [
            await _seed_chunk(
                pg_session, version_id=version_id, derivation_id=derivation_id, chunk_id=cid, company_id=company, text=body
            )
            for cid, body in texts.items()
        ]
        backend = PostgresSearchBackend(
            pg_session, semantic_enabled=True, embedding_model=provider.model_id
        )
        await backend.index(
            [
                _corpus_chunk(r, embedding=provider.embed_one(texts[r.chunk_id]))
                for r in rows
            ]
        )
        hits = await backend.search(
            CorpusQuery(
                text="cash",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.HYBRID,
                embedding=provider.embed_one(
                    "cash flow from operations improved during the period"
                ),
            )
        )
        assert hits, "the hybrid search returned nothing"
        assert hits[0].chunk.chunk_id == "ops"
        assert hits[0].semantic_score is not None
        assert hits[0].lexical_score is not None

    async def test_an_embedding_from_another_model_is_never_compared(
        self, pg_session
    ) -> None:
        provider = DeterministicEmbeddingProvider()
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        row = await _seed_chunk(
            pg_session, version_id=version_id, derivation_id=derivation_id, chunk_id="c1", company_id=company, text="revenue grew"
        )
        backend = PostgresSearchBackend(
            pg_session, semantic_enabled=True, embedding_model="model-a"
        )
        await backend.index([_corpus_chunk(row, embedding=provider.embed_one("revenue"))])
        # The search now runs configured for a DIFFERENT model.
        other = PostgresSearchBackend(
            pg_session, semantic_enabled=True, embedding_model="model-b"
        )
        hits = await other.search(
            CorpusQuery(
                text="revenue",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.HYBRID,
                embedding=provider.embed_one("revenue"),
            )
        )
        assert hits[0].semantic_score is None

    async def test_the_semantic_leg_is_off_by_default(self, pg_session) -> None:
        provider = DeterministicEmbeddingProvider()
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        row = await _seed_chunk(
            pg_session, version_id=version_id, derivation_id=derivation_id, chunk_id="c1", company_id=company, text="revenue grew"
        )
        backend = PostgresSearchBackend(pg_session, embedding_model=provider.model_id)
        await backend.index([_corpus_chunk(row, embedding=provider.embed_one("revenue"))])
        hits = await backend.search(
            CorpusQuery(
                text="revenue",
                filters=CorpusFilters(company_ids=(company,)),
                mode=SearchMode.HYBRID,
                embedding=provider.embed_one("revenue"),
            )
        )
        assert hits[0].semantic_score is None

    async def test_the_gin_expression_index_is_the_one_postgres_uses(
        self, pg_session
    ) -> None:
        """Not a performance test — a statement that the index migration 032 creates is
        the one this query can use. An index nothing plans against is a maintenance
        cost with no benefit."""
        await pg_session.execute(sa_text("SET enable_seqscan = off"))
        plan = (
            await pg_session.execute(
                sa_text(
                    "EXPLAIN SELECT 1 FROM research_document_chunks "
                    "WHERE to_tsvector('simple', text) @@ to_tsquery('simple', 'revenue')"
                )
            )
        ).scalars().all()
        await pg_session.execute(sa_text("SET enable_seqscan = on"))
        assert any("ix_research_document_chunks_fts" in line for line in plan), plan

    async def test_the_in_memory_and_postgres_backends_rank_the_same_top_hit(
        self, pg_session
    ) -> None:
        """Two backends, one contract. If switching them changed the answer, every
        benchmark comparison would be a comparison of two systems."""
        company = uuid.uuid4()
        pg_session.add(
            Company(id=company, ticker="X", exchange="Y", name="X", status="new")
        )
        await pg_session.flush()
        version_id, derivation_id = await _corpus_chain(pg_session)
        texts = {
            "a": "Operating margin declined on adverse currency movements.",
            "b": "The company opened forty new concept stores in the period.",
            "c": "Operating margin was supported by pricing and mix.",
        }
        rows = [
            await _seed_chunk(pg_session, chunk_id=cid, company_id=company, text=body, version_id=version_id, derivation_id=derivation_id)
            for cid, body in texts.items()
        ]
        pg = PostgresSearchBackend(pg_session)
        await pg.index([_corpus_chunk(r) for r in rows])
        memory = InMemorySearchBackend()
        await memory.index([_row_as_corpus_with_text(r) for r in rows])
        query = CorpusQuery(
            text="operating margin",
            filters=CorpusFilters(company_ids=(company,)),
            mode=SearchMode.LEXICAL,
            top_k=3,
        )
        pg_ids = {h.chunk.chunk_id for h in await pg.search(query)}
        mem_ids = {h.chunk.chunk_id for h in await memory.search(query)}
        assert pg_ids == mem_ids == {"a", "c"}


def _row_as_corpus_with_text(row: ResearchDocumentChunk) -> CorpusChunk:
    chunk = _row_as_corpus(row)
    return CorpusChunk(**{**chunk.__dict__, "text": row.text})
