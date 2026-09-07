"""Chunk persistence and index population — V3.1 Slice 1.5.

Two steps that are deliberately separable:

**Persist.** Chunks are corpus *data*. They live in PostgreSQL beside the pages
and tables they came from, they carry the citation lineage, and they exist whether
or not a search backend has ever been configured. This is what makes the backend
decision (OPEN DECISION #1) cheap to defer and cheap to change: switching backends
reindexes from rows that are already there rather than re-parsing every document.

**Index.** Pushing those rows into a ``SearchBackend``. Optional, idempotent,
and driven by whatever backend the caller supplies — no backend is resolved from
configuration here, because there is no production backend to resolve yet.

GOVERNANCE IS APPLIED AT PERSISTENCE, NOT AT QUERY TIME
=======================================================
``indexable`` is computed once, from the version's access class, and stored on the
chunk. A policy check that only happens on the way out is a policy check that is
skipped by whichever read path forgets it; stamping the chunk means the constraint
travels with the data into every backend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from app.models.research_chunk import CHUNK_KIND_TABLE, ResearchDocumentChunk
from app.models.research_derivation import (
    ResearchDocumentDerivation,
    ResearchDocumentTable,
)
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.corpus.chunking import build_chunks
from app.services.corpus.policy import default_policy_for
from app.services.corpus.search.types import CorpusChunk, IndexResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.core.config import Settings
    from app.services.corpus.parsed import ParsedDocument
    from app.services.corpus.search.types import SearchBackend


@dataclass
class ChunkingResult:
    """Secret-free counts of what the chunk writer did."""

    chunks_created: int = 0
    chunks_reused: int = 0
    tables_linked: int = 0


async def persist_chunks(
    session: "Any",
    *,
    version: ResearchDocumentVersion,
    derivation: ResearchDocumentDerivation,
    parsed: "ParsedDocument",
    cfg: "Settings",
    result: ChunkingResult | None = None,
) -> "list[ResearchDocumentChunk]":
    """Build and store the retrieval units for one derivation. Flush-only.

    Idempotent: a derivation that already has chunks returns them untouched.
    Chunk ids are derived from the document's own coordinates, so re-running this
    over the same derivation would produce the same ids anyway — returning early
    is an optimisation, not the thing that makes it safe.
    """
    counts = result if result is not None else ChunkingResult()
    if not getattr(cfg, "v3_corpus_enabled", False):
        return []

    existing = (
        (
            await session.execute(
                select(ResearchDocumentChunk)
                .where(ResearchDocumentChunk.derivation_id == derivation.id)
                .order_by(ResearchDocumentChunk.ordinal)
            )
        )
        .scalars()
        .all()
    )
    if existing:
        counts.chunks_reused += len(existing)
        return list(existing)

    built = build_chunks(
        parsed, research_document_version_id=version.id, cfg=cfg
    )
    if not built:
        return []

    # Governance travels with the data: whether this text may enter an index is
    # decided once, here, from the version's own access class.
    policy = default_policy_for(version.access_class)
    table_ids = await _table_ids_by_location(session, derivation_id=derivation.id)

    rows: list[ResearchDocumentChunk] = []
    for chunk in built:
        table_id = (
            table_ids.get(chunk.table_location or "")
            if chunk.kind == CHUNK_KIND_TABLE
            else None
        )
        if table_id is not None:
            counts.tables_linked += 1
        rows.append(
            ResearchDocumentChunk(
                id=uuid.uuid4(),
                chunk_id=chunk.chunk_id,
                derivation_id=derivation.id,
                research_document_version_id=version.id,
                company_id=None,
                research_document_table_id=table_id,
                kind=chunk.kind,
                ordinal=chunk.ordinal,
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=chunk.section_path,
                table_location=chunk.table_location,
                document_type=None,
                source_tier=version.source_tier,
                access_class=version.access_class,
                period_key=version.period_key,
                period_type=version.period_type,
                scope_type=chunk.scope_type,
                scope_name=chunk.scope_name,
                scope_key=chunk.scope_key,
                language=version.language,
                published_at=version.published_at,
                indexable=policy.indexed,
            )
        )

    document = await session.get(ResearchDocument, version.research_document_id)
    for row in rows:
        row.company_id = document.company_id if document else None
        row.document_type = document.document_type if document else None
        session.add(row)
    counts.chunks_created += len(rows)
    await session.flush()
    return rows


async def _table_ids_by_location(
    session: "Any", *, derivation_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """Map ``table_location`` → stored grid id, so a table chunk can point at it."""
    rows = (
        (
            await session.execute(
                select(
                    ResearchDocumentTable.table_location, ResearchDocumentTable.id
                ).where(ResearchDocumentTable.derivation_id == derivation_id)
            )
        )
        .tuples()
        .all()
    )
    return {location: table_id for location, table_id in rows}


def to_corpus_chunks(
    rows: "Sequence[ResearchDocumentChunk]",
    *,
    version: ResearchDocumentVersion | None = None,
) -> "list[CorpusChunk]":
    """Turn stored rows into the search contract's shape.

    Every field a citation needs is carried across, so a hit does not require a
    second lookup to be renderable — which is the property Slice 1.6 is built on.
    """
    out: list[CorpusChunk] = []
    for row in rows:
        out.append(
            CorpusChunk(
                chunk_id=row.chunk_id,
                text=row.text,
                research_document_id=(
                    version.research_document_id if version is not None else None
                ),
                research_document_version_id=row.research_document_version_id,
                derivation_id=row.derivation_id,
                company_id=row.company_id,
                canonical_url=version.canonical_url if version is not None else None,
                title=version.title if version is not None else None,
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
            )
        )
    return out


async def index_version(
    session: "Any",
    *,
    research_document_version_id: uuid.UUID,
    backend: "SearchBackend",
    cfg: "Settings",
    replace: bool = True,
) -> IndexResult:
    """Push one version's stored chunks into a search backend.

    ``replace=True`` deletes the version's existing index entries first, which is
    what a reindex after reprocessing needs: the previous derivation's chunks have
    different ids and would otherwise linger as a second, stale copy of the
    document.
    """
    if not getattr(cfg, "v3_corpus_enabled", False):
        return IndexResult()

    version = await session.get(ResearchDocumentVersion, research_document_version_id)
    if version is None:
        return IndexResult()

    active = (
        await session.execute(
            select(ResearchDocumentDerivation)
            .where(
                ResearchDocumentDerivation.research_document_version_id == version.id,
                ResearchDocumentDerivation.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is None:
        # No active derivation means nothing has been parsed under current
        # semantics. Indexing a superseded derivation would put a stale reading of
        # the document in front of a researcher.
        return IndexResult()

    rows = (
        (
            await session.execute(
                select(ResearchDocumentChunk)
                .where(ResearchDocumentChunk.derivation_id == active.id)
                .order_by(ResearchDocumentChunk.ordinal)
            )
        )
        .scalars()
        .all()
    )
    if replace:
        await backend.delete(research_document_version_id=version.id)
    if not rows:
        return IndexResult()
    return await backend.index(to_corpus_chunks(rows, version=version))


async def drop_chunks_for_derivation(
    session: "Any", *, derivation_id: uuid.UUID
) -> int:
    """Delete the chunks of ONE derivation. Used only when rebuilding them.

    Deliberately narrow: it never touches another derivation's chunks, so the
    superseded reading of a document keeps its retrieval units and an old citation
    keeps resolving.
    """
    rows = (
        (
            await session.execute(
                select(ResearchDocumentChunk.id).where(
                    ResearchDocumentChunk.derivation_id == derivation_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    await session.execute(
        delete(ResearchDocumentChunk).where(
            ResearchDocumentChunk.derivation_id == derivation_id
        )
    )
    await session.flush()
    return len(rows)


__all__ = [
    "ChunkingResult",
    "drop_chunks_for_derivation",
    "index_version",
    "persist_chunks",
    "to_corpus_chunks",
]
