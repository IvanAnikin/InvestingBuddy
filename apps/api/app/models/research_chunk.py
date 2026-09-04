"""The retrieval unit — V3.1 Slice 1.5.

    ResearchDocumentDerivation ──< ResearchDocumentChunk

A chunk is what search returns and what a citation points at. It is deliberately
NOT a page and NOT a section: a page is a printing artifact and a section can run
for twenty of them, and neither is the right size to put in front of a reader as
"here is where this came from".

WHY THE FILTER KEYS ARE COPIED ONTO EVERY ROW
=============================================
Entity, period, scope, tier, document type, language and access class are all
denormalized here even though every one of them is reachable by a join. That is
because they must be applied *inside* the search, not after it: a retrieval that
takes the ten best matches and then discards the wrong-period ones has returned
the ten best matches from the wrong periods, and the right ten were never
retrieved. A join the search backend cannot perform is a filter that will not be
applied.

The same copies are what let a hit render a citation without a second lookup.

WHY A TABLE CHUNK POINTS AT A GRID INSTEAD OF REPLACING IT
==========================================================
``docs/v3/DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.2 says tables are not chunks: a
borderless five-year summary must survive as a grid, because its column→year map
is the whole reason the geometric reconstructor exists. It must also be
*findable*, or an agent cannot reach it at all.

So both. ``ResearchDocumentTable`` stays the authoritative grid, and a table chunk
is a searchable surface that carries ``research_document_table_id`` — a hit
resolves to the grid, never to the flattened text it was found by.
"""

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Prose and tables are chunked differently and cited differently, so the kind is
#: a column rather than something inferred from whether a table id is set.
CHUNK_KIND_PROSE = "prose"
CHUNK_KIND_TABLE = "table"


class ResearchDocumentChunk(Base):
    """One retrievable span of one derivation."""

    __tablename__ = "research_document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The STABLE identity a citation and a search index both use. Derived from
    #: the document version, the pipeline version and the span's own coordinates —
    #: never assigned by the index — so reindexing the same derivation puts the
    #: same span back in the same slot. See ``app.services.corpus.chunking``.
    chunk_id: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    derivation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_derivations.id",
            ondelete="CASCADE",
            name="fk_research_document_chunks_derivation_id",
        ),
        nullable=False,
    )
    research_document_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_versions.id",
            ondelete="CASCADE",
            name="fk_research_document_chunks_version_id",
        ),
        nullable=False,
    )
    #: Lineage, and `SET NULL` like every other company FK (CLAUDE.md rule 15). A
    #: chunk whose company row is gone stops being entity-scoped rather than
    #: disappearing with it.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_document_chunks_company_id",
        ),
        nullable=True,
    )
    #: Set only on a table chunk. The grid is the truth; the chunk is how it is
    #: found.
    research_document_table_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_tables.id",
            ondelete="CASCADE",
            name="fk_research_document_chunks_table_id",
        ),
        nullable=True,
    )
    #: ``prose`` | ``table``.
    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: Document order within the derivation. Stable, and what a reader means by
    #: "the next chunk".
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: Offsets into the derivation's full text (the ordered concatenation of its
    #: pages). Exact provenance, without a second copy of the document.
    char_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(sa.Integer)
    page_end: Mapped[int | None] = mapped_column(sa.Integer)
    section_path: Mapped[str | None] = mapped_column(sa.String(1000))
    table_location: Mapped[str | None] = mapped_column(sa.String(200))
    # -- denormalized filter keys; see the module note on why ---------------- #
    document_type: Mapped[str | None] = mapped_column(sa.String(50))
    source_tier: Mapped[str | None] = mapped_column(sa.String(50))
    access_class: Mapped[str | None] = mapped_column(sa.String(30))
    period_key: Mapped[str | None] = mapped_column(sa.String(20))
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_name: Mapped[str | None] = mapped_column(sa.String(200))
    scope_key: Mapped[str | None] = mapped_column(sa.String(220))
    language: Mapped[str | None] = mapped_column(sa.String(10))
    published_at: Mapped[date | None] = mapped_column(sa.Date)
    #: Whether governance permits this text to enter a search index. A chunk with
    #: ``False`` is stored and citable and is never retrievable by search.
    indexable: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        # The stable identity, unique across the corpus — a citation resolves to
        # exactly one chunk.
        sa.Index("ix_research_document_chunks_chunk_id", "chunk_id", unique=True),
        sa.Index(
            "ix_research_document_chunks_derivation_ordinal",
            "derivation_id",
            "ordinal",
            unique=True,
        ),
        sa.Index("ix_research_document_chunks_version_id", "research_document_version_id"),
        # The shape of nearly every corpus query: this company, this period.
        sa.Index("ix_research_document_chunks_company_period", "company_id", "period_key"),
        sa.Index("ix_research_document_chunks_scope_key", "scope_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchDocumentChunk {self.kind} #{self.ordinal} {self.chunk_id[:16]}>"


__all__ = ["CHUNK_KIND_PROSE", "CHUNK_KIND_TABLE", "ResearchDocumentChunk"]
