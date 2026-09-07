"""The parsed representation of a document version — V3.1 Slice 1.3.

    ResearchDocumentVersion ──< ResearchDocumentDerivation ──< Page
                                (one parser version)         ├─< Section
                                                             └─< Table

WHY A DERIVATION SITS BETWEEN THEM
==================================
A version is a *retrieval* and is immutable. What a parser made of it is not: the
extraction pipeline version is at 15, and versions 9-15 each changed how
already-fetched text is read — two-column reconstruction, a geometric table
rebuilder, a gutter threshold, a larger page budget. Hanging pages and tables
directly off the version would mean improving a parser silently rewrites the
record of what was retrieved, and there would be no way to answer "what did we
believe this document said, in March, under version 13?".

So each parse is its own row, stamped with the pipeline version it ran under.
Exactly one is ``is_active`` — enforced by a partial unique index — and the
others are kept. That is the same supersession philosophy ``ExtractedFact.is_active``
already applies to facts, raised one level to the representation itself, and it
is what makes Slice 1.7's reprocessing non-destructive by construction.

WHAT "COMPLETE" MEANS, AND WHY IT IS RECORDED
=============================================
The live extraction path reads at most ``primary_document_max_pdf_pages`` (40)
plus a targeted supplemental pass (12). A 169-page annual report therefore yields
a derivation holding 52 of 169 pages. That is a very large improvement on 20
bounded excerpts and it is still not the whole document, so ``pages_persisted``
and ``page_count`` are both stored and ``status`` says ``partial``. A corpus that
reported "not found" without distinguishing "we never read that page" from "that
page does not say it" would be worse than the excerpt model it replaces.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: One parse either read everything the document has, or it did not, or it failed.
#: Three values, and the middle one is the honest common case.
DERIVATION_COMPLETE = "complete"
DERIVATION_PARTIAL = "partial"
DERIVATION_FAILED = "failed"

#: How much of the document a parse was ALLOWED to read. The pages a parse could
#: open are part of what the parse is, so two runs of the same parser under
#: different budgets are two derivations rather than one.
#:
#: ``live`` is the request path, bounded by the gunicorn worker timeout — a bound
#: that drifted once and cost six outages. ``deep`` is a reprocessing run from the
#: retained bytes, off the request path, where nothing is waiting.
PROFILE_LIVE = "live"
PROFILE_DEEP = "deep"


class ResearchDocumentDerivation(Base):
    """One parse of one document version, under one pipeline version."""

    __tablename__ = "research_document_derivations"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_document_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_versions.id",
            ondelete="CASCADE",
            name="fk_research_document_derivations_version_id",
        ),
        nullable=False,
    )
    #: ``CURRENT_EXTRACTION_PIPELINE_VERSION`` when this parse ran. The reason the
    #: table exists: a derivation produced under version 13 must never be silently
    #: read as if it were produced under version 15.
    pipeline_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: ``native_pdf`` | ``html`` | ``ocr`` — the existing extraction vocabulary.
    extraction_method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    #: ``live`` | ``deep`` — which budget this parse ran under. Part of the
    #: derivation's identity, because re-running the same parser with a larger page
    #: cap produces a genuinely different and better reading of the document, and
    #: without this column the second run would find the first and skip.
    extraction_profile: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, default=PROFILE_LIVE, server_default=PROFILE_LIVE
    )
    #: ``complete`` | ``partial`` | ``failed``. See the module note on why
    #: ``partial`` is the honest common case for a long annual report.
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: Exactly one active derivation per version, enforced by a partial unique
    #: index. Superseded ones are kept — never deleted (CLAUDE.md rule 15) — so
    #: "what did this document say under the old parser" stays answerable.
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: How many pages this parse actually persisted.
    pages_persisted: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    #: How many pages the DOCUMENT has, when known. NULL means the parser could
    #: not tell — never assumed equal to ``pages_persisted``, because that would
    #: turn "we read 52 of 169" into a false claim of completeness.
    page_count: Mapped[int | None] = mapped_column(sa.Integer)
    #: True when page numbers are the document's OWN (a PDF). False for HTML,
    #: where "page 1" is a container rather than a printed page — so a citation
    #: layer knows when it may say "page 14".
    paginated: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    char_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    section_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    table_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    #: The parser's own honest "this was cut short" signal (byte cap, page cap,
    #: wall-clock budget). Carried through so a reader never mistakes a truncated
    #: parse for the whole document.
    truncated: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    language: Mapped[str | None] = mapped_column(sa.String(10))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_document_derivations_version_pipeline",
            "research_document_version_id",
            "pipeline_version",
            "extraction_profile",
            unique=True,
        ),
        sa.Index(
            "ix_research_document_derivations_one_active",
            "research_document_version_id",
            unique=True,
            postgresql_where=sa.text("is_active"),
            sqlite_where=sa.text("is_active"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ResearchDocumentDerivation v{self.pipeline_version}/"
            f"{self.extraction_profile} {self.status} active={self.is_active}>"
        )


class ResearchDocumentPage(Base):
    """The full extracted text of one page.

    Not an excerpt. The point of the corpus is that page 87 of a 169-page report
    is still there in six months, so a later question about it is answered from
    the corpus rather than by a re-fetch under a fresh timeout.

    ``char_start`` / ``char_end`` are this page's offsets into the derivation's
    full text, defined as the ordered concatenation of its pages. The full text is
    therefore *derived* rather than stored a second time, and an offset pair is
    enough to locate any span exactly.
    """

    __tablename__ = "research_document_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    derivation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_derivations.id",
            ondelete="CASCADE",
            name="fk_research_document_pages_derivation_id",
        ),
        nullable=False,
    )
    #: 1-based. For an HTML document there is exactly one page; see
    #: ``ResearchDocumentDerivation.paginated``.
    page_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    char_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    extraction_method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_document_pages_derivation_page",
            "derivation_id",
            "page_number",
            unique=True,
        ),
    )


class ResearchDocumentSection(Base):
    """A heading-delimited run of the document, with its scope resolved.

    ``heading_path`` is the structure the font-size heading stack and the HTML
    ancestor tracking already recover — "Financial statements > Segment
    information". It is what lets a retrieval hit say *where in the document* it
    came from rather than only which page.

    The scope columns are the same typed `(scope_type, scope_name, scope_key)`
    triple ``fact_scope`` defines and migration 018 added to facts. Group is not
    segment, an unrecognised heading is a segment rather than silently the Group,
    and unknown stays unknown.
    """

    __tablename__ = "research_document_sections"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    derivation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_derivations.id",
            ondelete="CASCADE",
            name="fk_research_document_sections_derivation_id",
        ),
        nullable=False,
    )
    #: Document order. Stable for a given derivation, which is what a citation
    #: into a section needs.
    section_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(sa.String(500))
    #: ``ancestor > heading``, as far as the extractor could resolve it.
    heading_path: Mapped[str | None] = mapped_column(sa.String(1000))
    page_start: Mapped[int | None] = mapped_column(sa.Integer)
    page_end: Mapped[int | None] = mapped_column(sa.Integer)
    char_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    scope_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_name: Mapped[str | None] = mapped_column(sa.String(200))
    scope_key: Mapped[str | None] = mapped_column(sa.String(220))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_document_sections_derivation_index",
            "derivation_id",
            "section_index",
            unique=True,
        ),
        sa.Index("ix_research_document_sections_scope_key", "scope_key"),
    )


class ResearchDocumentTable(Base):
    """A recovered table, kept as a GRID rather than flattened into text.

    ``docs/v3/DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.2 is explicit: tables are not
    chunks, and a borderless five-year summary must survive as a grid. Flattening
    it to ``" | ".join(row)`` — which is what the V2 evidence bridge does for the
    council — destroys the column→year mapping that
    ``financial_table_reconstructor`` worked to recover, and that mapping is the
    difference between "revenue 23,006" and "FY2023 revenue 23,006".
    """

    __tablename__ = "research_document_tables"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    derivation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_document_derivations.id",
            ondelete="CASCADE",
            name="fk_research_document_tables_derivation_id",
        ),
        nullable=False,
    )
    #: Document order within this derivation.
    table_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: The extractor's own locator — ``p12:t2`` (PDF) or ``t2`` (HTML) — kept
    #: verbatim so an existing fact's ``table_location`` joins straight to it.
    table_location: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    page_number: Mapped[int | None] = mapped_column(sa.Integer)
    rows_json: Mapped[list[list[str]]] = mapped_column(JSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    col_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: True when the grid was rebuilt GEOMETRICALLY from positioned words rather
    #: than recovered from ruling lines. A borderless multi-year summary has no
    #: rules to find, and this is the flag that says its column→period map came
    #: from an x-anchored header rather than from the document's default period.
    reconstructed: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    #: The period of each value column, in column order. Empty for a table whose
    #: columns are not periods — never a guess.
    column_periods: Mapped[list[str] | None] = mapped_column(JSONB)
    scope_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_name: Mapped[str | None] = mapped_column(sa.String(200))
    scope_key: Mapped[str | None] = mapped_column(sa.String(220))
    extraction_method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(sa.Float)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_research_document_tables_derivation_index",
            "derivation_id",
            "table_index",
            unique=True,
        ),
        sa.Index("ix_research_document_tables_scope_key", "scope_key"),
    )

    def rows(self) -> "list[list[Any]]":  # pragma: no cover - convenience accessor
        return list(self.rows_json or [])


__all__ = [
    "DERIVATION_COMPLETE",
    "DERIVATION_FAILED",
    "DERIVATION_PARTIAL",
    "PROFILE_DEEP",
    "PROFILE_LIVE",
    "ResearchDocumentDerivation",
    "ResearchDocumentPage",
    "ResearchDocumentSection",
    "ResearchDocumentTable",
]
