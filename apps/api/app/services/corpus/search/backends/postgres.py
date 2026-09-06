"""PostgreSQL corpus search — V3.4 Slice 4.9.

The production ``SearchBackend``, implementing [ADR-047](../../../../../docs/DECISIONS.md):
retrieval lives in the database that already holds the corpus, so an entity, period and
scope filter applies in the same transaction as the data it filters.

THE PROPERTY THIS BACKEND EXISTS TO HAVE
========================================
> **Filters apply inside the search, never after it.**

A retrieval that ranks the whole corpus and then discards the wrong periods has returned
the top ten matches from the wrong periods; the right ten were never retrieved. So every
filter is a ``WHERE`` clause in the same statement as the ``ORDER BY`` and the ``LIMIT``.
This is the same lesson V3.3.2 learned the expensive way — a filter in one layer and a
bound in another means the bound wins and the filter is decorative.

TWO LEGS, AND ONLY ONE OF THEM IS FINISHED
==========================================
**Lexical** is the production path and needs no extension: a GIN expression index over
``to_tsvector('simple', text)``, ranked by ``ts_rank_cd``. It works today, on the
PostgreSQL this project actually runs.

**Semantic** is feature-gated OFF and, without ``pgvector``, is a *rerank of a bounded
candidate pool* rather than an exhaustive nearest-neighbour search. ADR-053 records why:
``pg_available_extensions`` has no ``vector`` row here, and there is no embedding
provider that is not a fake. ``semantic_is_exhaustive`` is False and ``capabilities``
omits ``SearchMode.SEMANTIC``, because reporting a bounded rerank as a semantic search is
the kind of claim that survives right up until somebody measures recall.

DE-INDEXING IS AN UPDATE
========================
For every other backend, "remove this version from the index" is a deletion. Here the
chunk rows **are** the corpus: deleting them would destroy the spans old citations
resolve against. ``delete()`` therefore clears ``indexed_at``. A superseded derivation's
chunks stay citable and stop being retrievable, which is exactly what reprocessing needs.

WHAT THE QUERY STRING BECOMES
=============================
Tokens from the shared tokeniser, each wrapped as a quoted lexeme and OR-joined. OR
rather than AND because ``plainto_tsquery``'s AND makes a five-word question return
nothing on a corpus that says four of the words — ranking is what decides, not
membership. The tokeniser admits only ``[a-z0-9]`` runs with internal ``-``/``/``, so the
constructed query cannot carry a ``tsquery`` operator; it is passed as a bind parameter
regardless.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Float, bindparam, func, literal_column, select, text, update
from sqlalchemy.sql.elements import ColumnElement

from app.models.research_chunk import ResearchDocumentChunk
from app.services.corpus.search.backends.memory import tokenize
from app.services.corpus.search.embeddings import cosine
from app.services.corpus.search.fusion import (
    DEFAULT_LEXICAL_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
    reciprocal_rank_fusion,
)
from app.services.corpus.search.types import (
    CorpusChunk,
    CorpusFilters,
    CorpusHit,
    CorpusQuery,
    IndexResult,
    SearchMode,
)

#: The text-search configuration. ``'simple'`` on purpose — see ADR-053 and
#: ``fusion.py``: in a financial corpus the exact token usually deserves to win, and
#: ``FY2025`` / ``PNDORA`` / ``mRNA-1273`` survive it intact.
TS_CONFIG = "simple"

#: The configuration as SQL, not as a bind parameter. ``to_tsvector`` takes a
#: ``regconfig``, and a driver-typed ``VARCHAR`` does not resolve to one — PostgreSQL
#: answers ``function to_tsvector(character varying, text) does not exist``. Emitting the
#: literal is also what makes the query expression **byte-identical** to the one
#: migration 032 indexed, which is the difference between using the GIN index and
#: sequentially scanning the corpus. ``TS_CONFIG`` is a module constant and never user
#: input.
_TS_CONFIG_SQL: ColumnElement[Any] = literal_column(f"'{TS_CONFIG}'")

#: How many filtered rows the semantic leg may score without a vector index. A bound,
#: not a tuning knob: without ``pgvector`` the alternative is loading every chunk of
#: every filtered document into memory to compute cosine in Python.
DEFAULT_SEMANTIC_CANDIDATE_LIMIT = 500

#: How many lexical rows are fetched before fusion. Larger than ``top_k`` so the
#: semantic leg can promote something the lexical ranking placed twentieth — a fusion
#: over exactly ``top_k`` candidates cannot change the answer.
LEXICAL_FETCH_MULTIPLIER = 5
MAX_LEXICAL_FETCH = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_tsquery_string(query_text: str) -> str:
    """Tokens as OR-joined quoted lexemes, or ``""`` when there are none.

    Quoting each lexeme is what makes ``mrna-1273`` a single search term rather than a
    ``tsquery`` expression containing a ``-``. The tokeniser's character class already
    excludes every ``tsquery`` operator, so this is belt-and-braces rather than the only
    defence — and the result is still passed as a bind parameter.
    """
    tokens = tokenize(query_text)
    if not tokens:
        return ""
    return " | ".join(f"'{token}'" for token in tokens)


def _filter_clauses(filters: CorpusFilters) -> list[Any]:
    """Every constraint as a SQL predicate.

    Deliberately mirrors ``CorpusFilters.matches`` field for field. The two must agree,
    and a test asserts they do on the same data — a filter implemented in one backend
    and forgotten in another is how "FY2025 only" quietly starts meaning something else
    when the backend changes.
    """
    C = ResearchDocumentChunk
    clauses: list[Any] = []
    if filters.indexable_only:
        clauses.append(C.indexable.is_(True))
    if filters.company_ids:
        clauses.append(C.company_id.in_(list(filters.company_ids)))
    if filters.document_types:
        clauses.append(C.document_type.in_(list(filters.document_types)))
    if filters.source_tiers:
        clauses.append(C.source_tier.in_(list(filters.source_tiers)))
    if filters.access_classes:
        clauses.append(C.access_class.in_(list(filters.access_classes)))
    if filters.period_keys:
        clauses.append(C.period_key.in_(list(filters.period_keys)))
    if filters.period_types:
        clauses.append(C.period_type.in_(list(filters.period_types)))
    if filters.scope_types:
        clauses.append(C.scope_type.in_(list(filters.scope_types)))
    if filters.scope_keys:
        clauses.append(C.scope_key.in_(list(filters.scope_keys)))
    if filters.languages:
        clauses.append(C.language.in_(list(filters.languages)))
    if filters.published_from is not None:
        clauses.append(C.published_at.is_not(None))
        clauses.append(C.published_at >= filters.published_from)
    if filters.published_to is not None:
        clauses.append(C.published_at.is_not(None))
        clauses.append(C.published_at <= filters.published_to)
    return clauses


def _row_to_corpus_chunk(
    row: ResearchDocumentChunk, *, canonical_url: str | None, title: str | None
) -> CorpusChunk:
    embedding = row.embedding_json
    return CorpusChunk(
        chunk_id=row.chunk_id,
        text=row.text,
        research_document_version_id=row.research_document_version_id,
        derivation_id=row.derivation_id,
        company_id=row.company_id,
        canonical_url=canonical_url,
        title=title,
        page_start=row.page_start,
        page_end=row.page_end,
        section_path=row.section_path,
        char_start=row.char_start,
        char_end=row.char_end,
        table_location=row.table_location,
        document_type=row.document_type,
        source_tier=row.source_tier,
        access_class=row.access_class,
        period_key=row.period_key,
        period_type=row.period_type,
        scope_type=row.scope_type,
        scope_name=row.scope_name,
        scope_key=row.scope_key,
        language=row.language,
        published_at=row.published_at,
        indexable=row.indexable,
        embedding=tuple(float(v) for v in embedding) if embedding else None,
    )


class PostgresSearchBackend:
    """Corpus search inside PostgreSQL. Lexical always; semantic behind a flag."""

    name = "postgres"
    #: ``SEMANTIC`` is absent on purpose. Without ``pgvector`` the semantic leg cannot
    #: retrieve — it can only rerank what lexical retrieval already found — and a
    #: backend that declared the capability would let a caller request something it
    #: cannot deliver. ADR-053.
    capabilities = frozenset({SearchMode.LEXICAL, SearchMode.HYBRID})

    def __init__(
        self,
        session: Any,
        *,
        semantic_enabled: bool = False,
        semantic_candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
        embedding_model: str | None = None,
    ) -> None:
        self._session = session
        self._semantic_enabled = semantic_enabled
        self._semantic_candidate_limit = max(1, semantic_candidate_limit)
        self._embedding_model = embedding_model or None

    @property
    def semantic_is_exhaustive(self) -> bool:
        """False, and it matters.

        Without a vector index the semantic leg scores a bounded candidate pool. It can
        reorder what lexical retrieval found and it cannot find what lexical retrieval
        missed. Saying so here is the difference between a documented limitation and a
        recall figure nobody checks.
        """
        return False

    # -- indexing ---------------------------------------------------------- #

    async def index(self, chunks: "list[CorpusChunk]") -> IndexResult:
        """Mark chunks indexed, and store any embeddings they carry.

        The text is already in ``research_document_chunks`` — this backend does not keep
        a second copy of the corpus, which is the whole argument for putting retrieval
        in the database that holds it. What ``index`` writes is the *index state*.

        A chunk whose governance forbids indexing is skipped and counted, never written
        with ``indexed_at`` set: the permission is checked here as well as in the query
        filter, because a row that is in the index and filtered out at read time is one
        forgotten ``WHERE`` clause away from being returned.
        """
        indexed = updated = skipped = 0
        for chunk in chunks:
            if not chunk.indexable:
                skipped += 1
                continue
            row = (
                await self._session.execute(
                    select(ResearchDocumentChunk).where(
                        ResearchDocumentChunk.chunk_id == chunk.chunk_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                # The corpus row must exist first. Creating one here would let the
                # index invent a document the corpus has no lineage for.
                skipped += 1
                continue
            was_indexed = row.indexed_at is not None
            row.indexed_at = _utcnow()
            if chunk.embedding is not None:
                if not self._embedding_model:
                    raise ValueError(
                        "an embedding cannot be stored without the id of the model "
                        "that produced it: two models' vectors share a dimension and "
                        "nothing else, and comparing them returns a number that means "
                        "nothing."
                    )
                row.embedding_json = [float(v) for v in chunk.embedding]
                row.embedding_model = self._embedding_model
                row.embedding_dim = len(chunk.embedding)
            if was_indexed:
                updated += 1
            else:
                indexed += 1
        await self._session.flush()
        return IndexResult(
            indexed=indexed, updated=updated, skipped_not_indexable=skipped
        )

    async def delete(self, *, research_document_version_id: uuid.UUID) -> int:
        """De-index one version's chunks. **Never deletes them.**

        The rows are the corpus. A citation recorded last quarter still has to resolve,
        so what is removed is retrievability, not the span.
        """
        stmt = (
            update(ResearchDocumentChunk)
            .where(
                ResearchDocumentChunk.research_document_version_id
                == research_document_version_id,
                ResearchDocumentChunk.indexed_at.is_not(None),
            )
            .values(indexed_at=None)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    # -- searching --------------------------------------------------------- #

    async def search(self, query: CorpusQuery) -> "list[CorpusHit]":
        query.validate()
        tsquery = to_tsquery_string(query.text)
        if not tsquery:
            return []

        lexical_rows = await self._lexical(query, tsquery)
        if not lexical_rows:
            return []

        lexical_scores = {row.chunk_id: score for row, score in lexical_rows}
        rows_by_id = {row.chunk_id: row for row, _ in lexical_rows}

        semantic_scores: dict[str, float] = {}
        if (
            query.mode is SearchMode.HYBRID
            and query.embedding
            and self._semantic_enabled
        ):
            semantic_scores = self._semantic(query, rows_by_id)

        if not semantic_scores:
            ordered = sorted(
                lexical_scores.items(), key=lambda kv: (-kv[1], kv[0])
            )[: query.top_k]
            return [
                await self._hit(
                    rows_by_id[cid],
                    score=score,
                    lexical=score,
                    semantic=None,
                    query=query,
                )
                for cid, score in ordered
            ]

        fused = reciprocal_rank_fusion(
            [
                (
                    [
                        cid
                        for cid, _ in sorted(
                            lexical_scores.items(), key=lambda kv: (-kv[1], kv[0])
                        )
                    ],
                    DEFAULT_LEXICAL_WEIGHT,
                ),
                (
                    [
                        cid
                        for cid, _ in sorted(
                            semantic_scores.items(), key=lambda kv: (-kv[1], kv[0])
                        )
                    ],
                    DEFAULT_SEMANTIC_WEIGHT,
                ),
            ]
        )
        ordered_fused = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[
            : query.top_k
        ]
        return [
            await self._hit(
                rows_by_id[cid],
                score=score,
                lexical=lexical_scores.get(cid),
                semantic=semantic_scores.get(cid),
                query=query,
            )
            for cid, score in ordered_fused
        ]

    async def _lexical(
        self, query: CorpusQuery, tsquery: str
    ) -> "list[tuple[ResearchDocumentChunk, float]]":
        """Rank by ``ts_rank_cd`` with every filter in the same statement.

        ``ts_rank_cd`` rather than ``ts_rank``: cover density rewards matched terms
        appearing NEAR each other, which is what distinguishes a page that discusses
        Group revenue for FY2025 from one that mentions revenue in one paragraph and
        FY2025 in another.
        """
        C = ResearchDocumentChunk
        vector = func.to_tsvector(_TS_CONFIG_SQL, C.text)
        tsq = func.to_tsquery(_TS_CONFIG_SQL, bindparam("tsq", tsquery))
        rank = func.ts_rank_cd(vector, tsq).cast(Float)
        fetch = min(
            MAX_LEXICAL_FETCH, max(query.top_k, query.top_k * LEXICAL_FETCH_MULTIPLIER)
        )
        stmt = (
            select(C, rank.label("rank"))
            .where(
                C.indexed_at.is_not(None),
                vector.op("@@")(tsq),
                *_filter_clauses(query.filters),
            )
            .order_by(rank.desc(), C.chunk_id)
            .limit(fetch)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], float(row[1] or 0.0)) for row in rows]

    def _semantic(
        self, query: CorpusQuery, rows_by_id: "dict[str, ResearchDocumentChunk]"
    ) -> dict[str, float]:
        """Cosine over the lexical candidate pool. A rerank, not a retrieval.

        Two guards worth naming. An embedding produced by a different model is skipped
        rather than compared — the comparison would return a plausible number that means
        nothing. And the pool is bounded, so a pathological filter cannot turn a search
        into a full-corpus scan in Python.
        """
        wanted_model = self._embedding_model
        scores: dict[str, float] = {}
        for index, (chunk_id, row) in enumerate(rows_by_id.items()):
            if index >= self._semantic_candidate_limit:
                break
            if not row.embedding_json:
                continue
            if wanted_model and row.embedding_model != wanted_model:
                continue
            similarity = cosine(
                query.embedding, tuple(float(v) for v in row.embedding_json)
            )
            if similarity > 0.0:
                scores[chunk_id] = similarity
        return scores

    async def _hit(
        self,
        row: ResearchDocumentChunk,
        *,
        score: float,
        lexical: float | None,
        semantic: float | None,
        query: CorpusQuery,
    ) -> CorpusHit:
        canonical_url, title = await self._version_lineage(
            row.research_document_version_id
        )
        chunk = _row_to_corpus_chunk(row, canonical_url=canonical_url, title=title)
        wanted = set(tokenize(query.text))
        present = set(tokenize(row.text))
        return CorpusHit(
            chunk=chunk,
            score=score,
            lexical_score=lexical,
            semantic_score=semantic,
            matched_terms=tuple(sorted(wanted & present)),
        )

    async def _version_lineage(
        self, version_id: uuid.UUID
    ) -> "tuple[str | None, str | None]":
        """The canonical URL and title a citation needs, cached per search.

        Cached because a top-10 result set is routinely ten chunks of one document, and
        ten identical lookups is the N+1 that makes a fast query look slow. The cache
        lives for one backend instance, which is one request.
        """
        cache = getattr(self, "_lineage_cache", None)
        if cache is None:
            cache = {}
            self._lineage_cache: dict[uuid.UUID, tuple[str | None, str | None]] = cache
        if version_id in cache:
            return cache[version_id]
        row = (
            await self._session.execute(
                text(
                    "SELECT canonical_url, title FROM research_document_versions "
                    "WHERE id = :vid"
                ).bindparams(vid=version_id)
            )
        ).first()
        value = (row[0], row[1]) if row else (None, None)
        cache[version_id] = value
        return value


__all__ = [
    "DEFAULT_SEMANTIC_CANDIDATE_LIMIT",
    "TS_CONFIG",
    "PostgresSearchBackend",
    "to_tsquery_string",
]
