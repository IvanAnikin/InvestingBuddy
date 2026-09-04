"""The corpus document records — V3.1 Slice 1.2.

    ResearchArtifact  ──<  ResearchDocumentVersion  >──  ResearchDocument
      (bytes)                (one retrieval)               (the logical document)

WHY TWO TABLES
==============
A content hash identifies bytes; it cannot say whether two retrievals are the
same *document*. A restated annual report, a re-typeset PDF and the same report
served from a different CDN path are three hashes and one document. Collapsing
them into one row means the restatement silently overwrites the original and
every citation into the old text stops meaning what it said. Keeping them as
versions of one document is what makes a restatement visible as a delta rather
than as mutation — and it is why old citations keep resolving.

`ResearchDocument` is deliberately thin. It holds identity and nothing that could
contradict a version: the *bytes*, the period, the language, the status and the
tier all belong to a specific retrieval, and putting a second copy on the parent
would create two places for the same fact to disagree.

WHAT IS NOT HERE
================
No parsed text, no pages, no sections, no chunks. A version is a retrieval; what
was made *of* it is a derivation, it depends on which parser ran, and it belongs
to Slice 1.3. Storing extracted text on the version would make the version mutable
every time the parser improved, which is exactly the property "immutable version"
exists to prevent.

No `legal_entity_id` either. The entity master is V3.2; until then the corpus
links to `companies` exactly as every other table does, and V3.2's backfill gives
`companies` its entity link in one place rather than this table acquiring a second
one.
"""

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchDocument(Base):
    """The logical document — "Pandora Annual Report 2025" — across retrievals."""

    __tablename__ = "research_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Lineage, and `SET NULL` like every other company FK: deleting a company must
    #: not delete the record that research was performed for it (CLAUDE.md rule 15).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_documents_company_id_companies",
        ),
        nullable=True,
    )
    #: The stable logical identity within one company — ``annual_report:2025`` when
    #: the document declares what it is, ``url:<digest>`` when it does not. See
    #: ``app.services.corpus.identity`` for why the fallback asserts as little as
    #: it does.
    document_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: The discovery layer's own vocabulary, reused rather than reinvented.
    document_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    #: Best-known human title. Advisory only — never an identity.
    title: Mapped[str | None] = mapped_column(sa.String(500))
    #: The period this document is *about*, in ``ReportingPeriod.key`` form
    #: (``2025`` | ``2026-H1`` | ``2026-Q2`` | ``2025/26``). NULL means the document
    #: did not state one, which is a real answer and never coerced to a year.
    period_key: Mapped[str | None] = mapped_column(sa.String(20))
    #: ``annual`` | ``half`` | ``quarter`` | ``split_year``. NULL with a NULL
    #: ``period_key``. Annual ≠ interim is a semantic the corpus carries, not a
    #: display label.
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    language: Mapped[str | None] = mapped_column(sa.String(10))
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # The identity, enforced by the database rather than by a read-then-write
        # check two concurrent ingestions can both pass.
        #
        # PostgreSQL treats NULLs as distinct in a unique index, so a document with
        # no company never collides with another. That is the safe direction: an
        # unattributed document staying separate is a duplicate, whereas merging two
        # would attribute one issuer's pages to another.
        sa.Index(
            "ix_research_documents_company_key",
            "company_id",
            "document_key",
            unique=True,
        ),
        sa.Index("ix_research_documents_company_id", "company_id"),
        # "Which documents cover FY2025?" is the corpus's most common filter and it
        # must not become a sequential scan.
        sa.Index("ix_research_documents_period_key", "period_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchDocument {self.document_type} {self.document_key}>"


class ResearchDocumentVersion(Base):
    """One retrieval of one document. Immutable once written.

    "Immutable" is the load-bearing word. Everything mutable about a document —
    what a parser made of it, which pages were read, which facts survived — hangs
    off a *derivation* (Slice 1.3) instead, so improving a parser never rewrites
    the record of what was retrieved.
    """

    __tablename__ = "research_document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: A version without its document is meaningless — this is composition, not
    #: lineage, and it follows the same ``CASCADE`` the repository already uses for
    #: ``extracted_facts`` → ``extracted_documents``. Documents are never deleted;
    #: the cascade exists so the schema cannot hold an orphan, not as a policy.
    research_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_documents.id",
            ondelete="CASCADE",
            name="fk_research_document_versions_document_id",
        ),
        nullable=False,
    )
    #: SHA-256 of the raw bytes. The retrieval identity, and the same value
    #: ``ExtractedDocument.content_hash`` and ``ResearchArtifact.content_hash``
    #: carry, so the three records join without a second hash.
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: The retained bytes, when there are any. NULL means the bytes were not
    #: retained — policy, or a retention sweep — and the citation still resolves
    #: through ``canonical_url``.
    research_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_artifacts.id",
            ondelete="SET NULL",
            name="fk_research_document_versions_artifact_id",
        ),
        nullable=True,
    )
    #: The V2 record this version corresponds to. The compatibility bridge: a
    #: corpus read for a document not yet re-ingested falls back to that row's
    #: bounded ``excerpts_json`` rather than answering "not available".
    extracted_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "extracted_documents.id",
            ondelete="SET NULL",
            name="fk_research_document_versions_extracted_document_id",
        ),
        nullable=True,
    )
    #: Secret-stripped and percent-encoding-normalised by ``canonicalize_source_url``.
    canonical_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    media_type: Mapped[str | None] = mapped_column(sa.String(100))
    byte_size: Mapped[int | None] = mapped_column(sa.Integer)
    title: Mapped[str | None] = mapped_column(sa.String(500))
    language: Mapped[str | None] = mapped_column(sa.String(10))
    #: WHO DELIVERED THE BYTES — ``company_ir``, ``sec_edgar``, later a search
    #: provider. Deliberately separate from ``source_tier`` below: the transport
    #: must never set the tier, or a vendor could upgrade the trustworthiness of
    #: everything it touches.
    transport: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    #: WHO PUBLISHED THE CONTENT, when that differs from the transport and is
    #: known. NULL is an honest "not recorded", never "same as transport".
    content_origin: Mapped[str | None] = mapped_column(sa.String(200))
    #: How much trust the CONTENT earns, in the existing ``taxonomy`` vocabulary.
    source_tier: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    #: Governance data class, mirrored from the artifact so a corpus query can
    #: filter on it without a join.
    access_class: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    #: The period this retrieval covers, and how that was decided
    #: (``document_period`` basis vocabulary). Kept per version because a
    #: restatement can correct it.
    period_key: Mapped[str | None] = mapped_column(sa.String(20))
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    period_basis: Mapped[str | None] = mapped_column(sa.String(40))
    #: The document's own publication date when it states one. Never inferred from
    #: a retrieval timestamp — "when we fetched it" is not "when it was published".
    published_at: Mapped[date | None] = mapped_column(sa.Date)
    retrieved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    #: ``extracted`` | ``metadata_only`` | ``extraction_failed`` — the existing
    #: honest vocabulary. A blocked or scanned document is recorded as a version
    #: with an honest status, never omitted and never recorded as if it were read.
    extraction_status: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    #: A member of the closed ``ingestion_status`` vocabulary when the retrieval did
    #: not reach ``extracted``. Never raw exception text.
    failure_code: Mapped[str | None] = mapped_column(sa.String(50))
    #: Exactly one version per document is the current representation. Older ones
    #: stay, and their citations keep resolving — that is the entire point of
    #: versioning rather than overwriting.
    is_current: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    #: When this version stopped being current. NULL for the current one and for
    #: any version that was never current.
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # The same bytes are one version of a document, not two.
        sa.Index(
            "ix_research_document_versions_document_hash",
            "research_document_id",
            "content_hash",
            unique=True,
        ),
        # "Exactly one current version" as a DATABASE constraint rather than as a
        # convention the writer is trusted to maintain. A partial unique index is
        # the only form that expresses it, and both PostgreSQL and SQLite support
        # one — so the invariant holds in the test suite too.
        sa.Index(
            "ix_research_document_versions_one_current",
            "research_document_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current"),
        ),
        sa.Index("ix_research_document_versions_content_hash", "content_hash"),
        sa.Index(
            "ix_research_document_versions_extracted_document_id",
            "extracted_document_id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchDocumentVersion {self.content_hash[:12]} current={self.is_current}>"


__all__ = ["ResearchDocument", "ResearchDocumentVersion"]
