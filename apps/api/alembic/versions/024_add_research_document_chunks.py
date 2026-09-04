"""Add the retrieval-unit table — V3.1 Slice 1.5.

PURPOSE
=======
``research_document_chunks``: what search returns and what a citation points at.

A chunk is deliberately neither a page nor a section. A page is a printing
artifact; a section of an annual report can run for twenty of them. Retrieving a
section answers "which section" when the question was "which sentence".

WHY THE FILTER KEYS ARE COPIED ONTO EVERY ROW
=============================================
``company_id``, ``document_type``, ``source_tier``, ``access_class``,
``period_key``, ``period_type``, the scope triple, ``language`` and
``published_at`` are all denormalized here even though each is reachable by a
join. They must be applied INSIDE the search rather than after it: a retrieval
that takes the ten best matches and then discards the wrong-period ones has
returned the ten best matches from the wrong periods, and the right ten were never
retrieved. A join a search backend cannot perform is a filter that will not be
applied. The same copies let a hit render a citation without a second lookup.

CHUNK IDENTITY IS DERIVED, NOT ASSIGNED
=======================================
``chunk_id`` is a hash of (version, pipeline version, kind, ordinal, offsets,
table location) — not a row id. A row id belongs to whichever database wrote it,
so rebuilding the corpus would invalidate every citation. Re-running the same
parser reproduces the same ids exactly; re-parsing under a NEW pipeline version
produces new ones, which is correct because it is a different reading of the
document, and the old derivation and its chunks are retained so the old citation
still resolves.

A TABLE CHUNK POINTS AT A GRID RATHER THAN REPLACING IT
=======================================================
``research_document_table_id`` is set on a table chunk. The grid in
``research_document_tables`` stays authoritative — its column→year map is the
whole reason the geometric reconstructor exists — and the chunk is the searchable
surface that makes it findable. A hit resolves to the grid, never to the flattened
text it was found by.

``char_start``/``char_end`` are 0 on a table chunk. That is honest rather than
lazy: a table has no span in the prose coordinate system, and its provenance is
its ``table_location`` and page.

ADDITIVE ONLY. One table, nothing existing touched. Nothing writes to it unless
``V3_CORPUS_ENABLED`` is on, which is off by default.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``ix_research_document_chunks_chunk_id`` (UNIQUE) — the stable identity; a
  citation resolves to exactly one chunk.
* ``ix_research_document_chunks_derivation_ordinal`` (UNIQUE) — document order,
  and one row per position.
* ``ix_research_document_chunks_version_id`` — "drop this version's chunks",
  which is what reindexing after a reprocessing run does.
* ``ix_research_document_chunks_company_period`` — the shape of nearly every
  corpus query: this company, this period.
* ``ix_research_document_chunks_scope_key`` — the Group-versus-segment filter.

FOREIGN KEYS
============
``derivation_id``, ``research_document_version_id`` and
``research_document_table_id`` are **CASCADE** (composition — a chunk without its
derivation is meaningless). ``company_id`` is **SET NULL** (lineage — CLAUDE.md
rule 15): a chunk whose company row is gone stops being entity-scoped rather than
disappearing with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_document_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chunk_id", sa.String(length=80), nullable=False),
        sa.Column("derivation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("research_document_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("research_document_table_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section_path", sa.String(length=1000), nullable=True),
        sa.Column("table_location", sa.String(length=200), nullable=True),
        sa.Column("document_type", sa.String(length=50), nullable=True),
        sa.Column("source_tier", sa.String(length=50), nullable=True),
        sa.Column("access_class", sa.String(length=30), nullable=True),
        sa.Column("period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_name", sa.String(length=200), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("indexable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["derivation_id"],
            ["research_document_derivations.id"],
            name="fk_research_document_chunks_derivation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_document_version_id"],
            ["research_document_versions.id"],
            name="fk_research_document_chunks_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_document_chunks_company_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_document_table_id"],
            ["research_document_tables.id"],
            name="fk_research_document_chunks_table_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_document_chunks_chunk_id",
        "research_document_chunks",
        ["chunk_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_chunks_derivation_ordinal",
        "research_document_chunks",
        ["derivation_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_chunks_version_id",
        "research_document_chunks",
        ["research_document_version_id"],
    )
    op.create_index(
        "ix_research_document_chunks_company_period",
        "research_document_chunks",
        ["company_id", "period_key"],
    )
    op.create_index(
        "ix_research_document_chunks_scope_key",
        "research_document_chunks",
        ["scope_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_document_chunks_scope_key", table_name="research_document_chunks"
    )
    op.drop_index(
        "ix_research_document_chunks_company_period",
        table_name="research_document_chunks",
    )
    op.drop_index(
        "ix_research_document_chunks_version_id", table_name="research_document_chunks"
    )
    op.drop_index(
        "ix_research_document_chunks_derivation_ordinal",
        table_name="research_document_chunks",
    )
    op.drop_index(
        "ix_research_document_chunks_chunk_id", table_name="research_document_chunks"
    )
    op.drop_table("research_document_chunks")
