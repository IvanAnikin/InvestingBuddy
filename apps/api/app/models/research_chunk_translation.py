"""A machine translation of one held corpus chunk — V3.20 slice 10.1 (spec §3.12).

The parallel English corpus. Each row is the English rendering of exactly one
original-language chunk (``research_document_chunks``), linked by the chunk row, its
stable ``chunk_id`` and the SHA-256 of the original text it translated — a chunk whose
text changes no longer matches its translation, which is then not used.

A translation is never evidence by itself. Citations keep the ORIGINAL chunk's evidence
id; search may match the English text, but what is returned, quoted and verified is the
original. A translation whose figures differ from the original's is not stored as text
(``status = refused``, ``reason = figures_changed``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUS_TRANSLATED = "translated"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"
STATUSES = (STATUS_TRANSLATED, STATUS_REFUSED, STATUS_FAILED)


class ResearchChunkTranslation(Base):
    __tablename__ = "research_chunk_translations"
    __table_args__ = (
        sa.UniqueConstraint("chunk_row_id", "target_language",
                            name="uq_research_chunk_translations_chunk_target"),
        sa.CheckConstraint(f"status IN {STATUSES!r}",
                           name="ck_research_chunk_translations_status"),
        sa.CheckConstraint("status <> 'translated' OR translated_text IS NOT NULL",
                           name="ck_research_chunk_translations_text"),
        sa.Index("ix_research_chunk_translations_version", "research_document_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    chunk_row_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("research_document_chunks.id", ondelete="CASCADE",
                      name="fk_research_chunk_translations_chunk"),
        nullable=False)
    chunk_id: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    research_document_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("research_document_versions.id", ondelete="CASCADE",
                      name="fk_research_chunk_translations_version"),
        nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL",
                      name="fk_research_chunk_translations_company"),
        nullable=True)
    source_language: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(sa.String(10), nullable=False,
                                                 default="en")
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(200))
    translated_text: Mapped[str | None] = mapped_column(sa.Text)
    #: SHA-256 of the original chunk text that was translated.
    original_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(sa.String(120))
    prompt_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    original_chars: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    translated_chars: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    completion_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    usage_estimated: Mapped[bool | None] = mapped_column(sa.Boolean)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


__all__ = ["STATUSES", "STATUS_FAILED", "STATUS_REFUSED", "STATUS_TRANSLATED",
           "ResearchChunkTranslation"]
