"""Add the parsed-representation tables — V3.1 Slice 1.3.

PURPOSE
=======
Persist what a parser made of a document version — pages, sections and tables —
as a DERIVATION stamped with the pipeline version it ran under.

    research_document_versions ──< research_document_derivations ──< pages
                                    (one parser version)          ├─< sections
                                                                  └─< tables

WHY THE DERIVATION LAYER EXISTS
===============================
A version is a retrieval and is immutable. What a parser made of it is not: the
extraction pipeline version is at **15**, and versions 9-15 each changed how
already-fetched text is read. Hanging pages and tables directly off the version
would mean improving a parser silently rewrites the record of what was retrieved,
and "what did we believe this document said, in March, under version 13?" would
be unanswerable.

So each parse is its own row. Exactly one is ``is_active``; the others are kept,
never deleted (CLAUDE.md rule 15). That is ``ExtractedFact.is_active``'s
supersession philosophy raised one level, and it is what makes Slice 1.7's
reprocessing non-destructive by construction rather than by discipline.

WHAT THIS REPLACES
==================
``extracted_documents.excerpts_json`` holds at most 20 bounded excerpts of ≤1,200
characters. These tables hold the FULL text of every page the extractor opened,
its heading structure, and its tables **as grids**. The excerpt model is not
removed — it stays as the compatibility path for documents not yet reprocessed.

ADDITIVE ONLY. Four new tables, nothing existing touched. Nothing writes to them
unless ``V3_CORPUS_ENABLED`` is on, which is off by default.

NULLABILITY IS A STATEMENT
==========================
``page_count`` NULL means the parser could not tell how many pages the document
has. It is never set equal to ``pages_persisted``, because that would turn "we
read 52 of 169" into a false claim of completeness — and a corpus that cannot
distinguish "we never read that page" from "that page does not say it" would be
worse than the excerpt model it replaces. ``status`` says ``partial`` for exactly
that case, which for a long annual report on the live extraction budget is the
honest common outcome.

``column_periods`` NULL/empty means the table's columns are not periods. Never a
guess: the whole value of the geometric reconstructor is that a column→year map
comes from an x-anchored header rather than from the document's default period.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``ix_research_document_derivations_version_pipeline`` (UNIQUE) — one parse per
  (version, pipeline version). Re-running the same parser is idempotent rather
  than accumulating identical derivations.
* ``ix_research_document_derivations_one_active`` (UNIQUE, PARTIAL) — "exactly
  one active derivation" as a constraint rather than a convention.
* ``ix_research_document_pages_derivation_page`` (UNIQUE) — page lookup, and one
  row per page.
* ``ix_research_document_sections_derivation_index`` /
  ``ix_research_document_tables_derivation_index`` (UNIQUE) — stable document
  order, which is what a citation into a section or a table needs.
* ``ix_research_document_sections_scope_key`` /
  ``ix_research_document_tables_scope_key`` — "the Group figures only", the
  filter that stops a segment number being read as a consolidated one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_document_derivations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_document_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("extraction_method", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pages_persisted", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("paginated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "section_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("table_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_document_version_id"],
            ["research_document_versions.id"],
            name="fk_research_document_derivations_version_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_document_derivations_version_pipeline",
        "research_document_derivations",
        ["research_document_version_id", "pipeline_version"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_derivations_one_active",
        "research_document_derivations",
        ["research_document_version_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )

    op.create_table(
        "research_document_pages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("derivation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("extraction_method", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["derivation_id"],
            ["research_document_derivations.id"],
            name="fk_research_document_pages_derivation_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_document_pages_derivation_page",
        "research_document_pages",
        ["derivation_id", "page_number"],
        unique=True,
    )

    op.create_table(
        "research_document_sections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("derivation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=500), nullable=True),
        sa.Column("heading_path", sa.String(length=1000), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_name", sa.String(length=200), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["derivation_id"],
            ["research_document_derivations.id"],
            name="fk_research_document_sections_derivation_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_document_sections_derivation_index",
        "research_document_sections",
        ["derivation_id", "section_index"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_sections_scope_key",
        "research_document_sections",
        ["scope_key"],
    )

    op.create_table(
        "research_document_tables",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("derivation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False),
        sa.Column("table_location", sa.String(length=200), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("rows_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("col_count", sa.Integer(), nullable=False),
        sa.Column(
            "reconstructed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("column_periods", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_name", sa.String(length=200), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=True),
        sa.Column("extraction_method", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["derivation_id"],
            ["research_document_derivations.id"],
            name="fk_research_document_tables_derivation_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_research_document_tables_derivation_index",
        "research_document_tables",
        ["derivation_id", "table_index"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_tables_scope_key",
        "research_document_tables",
        ["scope_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_document_tables_scope_key", table_name="research_document_tables"
    )
    op.drop_index(
        "ix_research_document_tables_derivation_index",
        table_name="research_document_tables",
    )
    op.drop_table("research_document_tables")
    op.drop_index(
        "ix_research_document_sections_scope_key", table_name="research_document_sections"
    )
    op.drop_index(
        "ix_research_document_sections_derivation_index",
        table_name="research_document_sections",
    )
    op.drop_table("research_document_sections")
    op.drop_index(
        "ix_research_document_pages_derivation_page", table_name="research_document_pages"
    )
    op.drop_table("research_document_pages")
    op.drop_index(
        "ix_research_document_derivations_one_active",
        table_name="research_document_derivations",
    )
    op.drop_index(
        "ix_research_document_derivations_version_pipeline",
        table_name="research_document_derivations",
    )
    op.drop_table("research_document_derivations")
