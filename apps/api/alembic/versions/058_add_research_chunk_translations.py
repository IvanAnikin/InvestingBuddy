"""Research chunk translations — V3.20 slice 10.1 (spec §3.12).

ONE TABLE
=========
``research_chunk_translations`` — the parallel English corpus: one row per original-
language chunk and target language (unique), linked to the chunk row (CASCADE: a chunk
dropped by reprocessing takes its translation with it), its stable ``chunk_id``, the
document version and the SHA-256 of the original text translated. ``status`` is
``translated`` (text present — CHECK), ``refused`` (e.g. the figures changed) or
``failed``; the provider, model, prompt version and token counts are kept for audit.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "058"
down_revision: str | None = "057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_chunk_translations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chunk_row_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(80), nullable=False),
        sa.Column("research_document_version_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("source_language", sa.String(10), nullable=False),
        sa.Column("target_language", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("translated_text", sa.Text(), nullable=True),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("original_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("translated_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_estimated", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_row_id"], ["research_document_chunks.id"],
                                ondelete="CASCADE",
                                name="fk_research_chunk_translations_chunk"),
        sa.ForeignKeyConstraint(["research_document_version_id"],
                                ["research_document_versions.id"], ondelete="CASCADE",
                                name="fk_research_chunk_translations_version"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL",
                                name="fk_research_chunk_translations_company"),
        sa.UniqueConstraint("chunk_row_id", "target_language",
                            name="uq_research_chunk_translations_chunk_target"),
        sa.CheckConstraint("status IN ('translated', 'refused', 'failed')",
                           name="ck_research_chunk_translations_status"),
        sa.CheckConstraint("status <> 'translated' OR translated_text IS NOT NULL",
                           name="ck_research_chunk_translations_text"),
    )
    op.create_index("ix_research_chunk_translations_version",
                    "research_chunk_translations", ["research_document_version_id"])


def downgrade() -> None:
    op.drop_index("ix_research_chunk_translations_version",
                  table_name="research_chunk_translations")
    op.drop_table("research_chunk_translations")
