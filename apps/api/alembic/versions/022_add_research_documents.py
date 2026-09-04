"""Add the corpus document + version tables — V3.1 Slice 1.2.

PURPOSE
=======
Separate the *logical* document from one *retrieval* of it.

    ResearchArtifact  ──<  research_document_versions  >──  research_documents
      (bytes, 021)            (one retrieval)                 (the document)

A content hash identifies bytes and cannot say whether two retrievals are the
same document. A restated annual report, a re-typeset PDF and the same report
served from a different CDN path are three hashes and one document. Recorded as
one row, the restatement silently overwrites the original and every citation into
the old text stops meaning what it said. Recorded as versions, the restatement is
visible as a delta and the old citations keep resolving — which is the property
``docs/v3/DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.2 states as "version, not
overwrite".

ADDITIVE ONLY. Creates two tables and touches nothing that exists, so code on
``release/v2-current`` runs unchanged against a database with this applied.
Nothing writes to either unless ``V3_CORPUS_ENABLED`` is on, which is off by
default.

FOREIGN KEYS, EACH CHOSEN DELIBERATELY
======================================
* ``research_documents.company_id`` → ``SET NULL``. Deleting a company must not
  delete the record that research was performed for it (CLAUDE.md rule 15). Same
  choice every other company FK in this schema makes.
* ``research_document_versions.research_document_id`` → ``CASCADE``. This is
  composition, not lineage: a version without its document is meaningless. It
  follows the ``extracted_facts`` → ``extracted_documents`` precedent. Documents
  are never deleted; the cascade exists so the schema cannot hold an orphan.
* ``research_artifact_id`` → ``SET NULL``. Bytes expire; lineage outlives them.
* ``extracted_document_id`` → ``SET NULL``. The V2 compatibility bridge, and a
  V2 row disappearing must not take the corpus record with it.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``ix_research_documents_company_key`` (UNIQUE) — the logical identity, enforced
  by the DATABASE rather than by a read-then-write check two concurrent
  ingestions can both pass. PostgreSQL treats NULLs as distinct, so a document
  with no company never collides with another: an unattributed document staying
  separate is a duplicate, whereas merging two would attribute one issuer's pages
  to another.
* ``ix_research_documents_company_id`` — "every document for this company", the
  corpus's most common lookup.
* ``ix_research_documents_period_key`` — "which documents cover FY2025".
* ``ix_research_document_versions_document_hash`` (UNIQUE) — the same bytes are
  one version of a document, not two.
* ``ix_research_document_versions_one_current`` (UNIQUE, PARTIAL on
  ``is_current``) — **"exactly one current version" as a constraint rather than
  as a convention the writer is trusted to maintain.** A partial unique index is
  the only form that expresses it.
* ``ix_research_document_versions_content_hash`` — "do we already hold these
  bytes, for any document".
* ``ix_research_document_versions_extracted_document_id`` — the bridge lookup
  from a V2 row to its corpus version.

NULLABILITY IS A STATEMENT
==========================
``period_key`` / ``period_type`` NULL mean the document did not state a period.
That is a real answer, and it is never coerced to a bare year — the
``INTERIM_AS_ANNUAL`` contradiction that ``document_period.py`` exists to prevent
starts exactly there. ``published_at`` NULL means the document states no
publication date; it is never filled from ``retrieved_at``, because "when we
fetched it" is not "when it was published". ``content_origin`` NULL means "not
recorded", never "same as the transport" — the transport must never set the tier.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("document_key", sa.String(length=120), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_documents_company_id_companies",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_research_documents_company_key",
        "research_documents",
        ["company_id", "document_key"],
        unique=True,
    )
    op.create_index(
        "ix_research_documents_company_id", "research_documents", ["company_id"]
    )
    op.create_index(
        "ix_research_documents_period_key", "research_documents", ["period_key"]
    )

    op.create_table(
        "research_document_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("research_artifact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("extracted_document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("canonical_url", sa.String(length=2000), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("transport", sa.String(length=100), nullable=False),
        sa.Column("content_origin", sa.String(length=200), nullable=True),
        sa.Column("source_tier", sa.String(length=50), nullable=False),
        sa.Column("access_class", sa.String(length=30), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("period_basis", sa.String(length=40), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extraction_status", sa.String(length=50), nullable=False),
        sa.Column("failure_code", sa.String(length=50), nullable=True),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_document_id"],
            ["research_documents.id"],
            name="fk_research_document_versions_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_artifact_id"],
            ["research_artifacts.id"],
            name="fk_research_document_versions_artifact_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["extracted_document_id"],
            ["extracted_documents.id"],
            name="fk_research_document_versions_extracted_document_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_research_document_versions_document_hash",
        "research_document_versions",
        ["research_document_id", "content_hash"],
        unique=True,
    )
    op.create_index(
        "ix_research_document_versions_one_current",
        "research_document_versions",
        ["research_document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_research_document_versions_content_hash",
        "research_document_versions",
        ["content_hash"],
    )
    op.create_index(
        "ix_research_document_versions_extracted_document_id",
        "research_document_versions",
        ["extracted_document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_document_versions_extracted_document_id",
        table_name="research_document_versions",
    )
    op.drop_index(
        "ix_research_document_versions_content_hash",
        table_name="research_document_versions",
    )
    op.drop_index(
        "ix_research_document_versions_one_current",
        table_name="research_document_versions",
    )
    op.drop_index(
        "ix_research_document_versions_document_hash",
        table_name="research_document_versions",
    )
    op.drop_table("research_document_versions")
    op.drop_index("ix_research_documents_period_key", table_name="research_documents")
    op.drop_index("ix_research_documents_company_id", table_name="research_documents")
    op.drop_index("ix_research_documents_company_key", table_name="research_documents")
    op.drop_table("research_documents")
