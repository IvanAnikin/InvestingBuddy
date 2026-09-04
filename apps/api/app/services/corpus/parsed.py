"""The parsed representation: pages, sections, tables — V3.1 Slice 1.3.

Turns one ``PrimaryDocumentExtraction`` into the durable structure a later
question can be answered from without re-fetching the document.

TWO HALVES, AND THE FIRST ONE IS PURE
=====================================
:func:`build_parsed_document` is a pure function of the extraction: no session, no
clock, no configuration beyond a cap. Everything about *what the structure is* —
where pages start and end, which blocks belong to which section, what scope a
heading implies — is decided there and is testable without a database. The second
half only writes it down.

THE FULL TEXT IS DERIVED, NOT STORED TWICE
==========================================
A derivation's full text is defined as the ordered concatenation of its pages,
joined by a single ``\\n``. Every page, section and (later) chunk records
``char_start``/``char_end`` into that concatenation. So:

* the full document text is reconstructible exactly, by ordering pages and
  joining them;
* any span is locatable exactly, by offset;
* nothing is stored twice, which for a 169-page annual report is the difference
  between one and two copies of about half a megabyte.

The join character is part of the contract. If it changed, every persisted offset
would silently shift by one per page — which is why it is a module constant with
this comment attached rather than a literal at three call sites.

WHAT A SECTION IS
=================
A run of consecutive blocks sharing the same ``(ancestor_heading, section)`` pair.
That is exactly the granularity the extractor's own structure recovery produces:
the font-size heading stack for PDFs (which is what fixed CFR's Specialist
Watchmakers scoping) and ancestor tracking for HTML. Inventing a finer or coarser
sectioning here would discard information the extractor worked to recover.

Scope is decided in two steps, and the order matters. First
``primary_document_extractor.infer_heading_scope`` asks whether the heading is a
scope signal *at all* — the same vetting the extractor already applies before it
puts a scope on a table. Only then does ``fact_scope.parse_scope`` turn that
signal into the typed triple, where an unrecognised label is a *segment* rather
than silently the Group.

Skipping the first step was a real defect, found by running a real 169-page
Pandora annual report through this code: ``parse_scope`` alone labelled the cover
headings "CONTENTS", "BIG" and "PICTURE" as business segments, because its
fail-closed rule — right for a string the extractor has already vetted — is
"anything non-empty is a named segment".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.research_derivation import (
    DERIVATION_COMPLETE,
    DERIVATION_PARTIAL,
    ResearchDocumentDerivation,
    ResearchDocumentPage,
    ResearchDocumentSection,
    ResearchDocumentTable,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.fact_scope import parse_scope, scope_columns
from app.services.sources.primary_document_extractor import infer_heading_scope

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from app.core.config import Settings

#: How page texts are joined to form a derivation's full text. Part of the
#: persisted contract: changing it would shift every stored offset by one per
#: page and silently invalidate every citation into the corpus.
PAGE_JOIN = "\n"

#: The page number an unpaginated document (HTML) is recorded under. HTML has no
#: printed pages, so this is a container rather than a claim — see
#: ``ResearchDocumentDerivation.paginated``, which is what tells a citation layer
#: whether it may say "page 14".
UNPAGINATED_PAGE_NUMBER = 1

_HEADING_MAX = 500
_HEADING_PATH_MAX = 1000
_METHOD_MAX = 50
_TABLE_LOCATION_MAX = 200
_LANGUAGE_MAX = 10

METHOD_HTML = "html"


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ParsedSection:
    section_index: int
    heading: str | None
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    scope_type: str | None
    scope_name: str | None
    scope_key: str | None


@dataclass(frozen=True)
class ParsedTable:
    table_index: int
    table_location: str
    page_number: int | None
    rows: list[list[str]]
    row_count: int
    col_count: int
    reconstructed: bool
    column_periods: list[str]
    scope_type: str | None
    scope_name: str | None
    scope_key: str | None
    extraction_method: str
    confidence: float | None


@dataclass
class ParsedDocument:
    """The whole parsed structure, before it touches a database."""

    pipeline_version: int
    extraction_method: str
    paginated: bool
    pages: list[ParsedPage] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    page_count: int | None = None
    char_count: int = 0
    truncated: bool = False
    language: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.pages or self.tables)

    @property
    def status(self) -> str:
        """``complete`` only when nothing was cut short and every page was read.

        A 169-page annual report on the live extraction budget yields 52 pages and
        therefore ``partial``. Saying ``complete`` would make "the corpus has no
        answer" indistinguishable from "the document does not say it", which is
        the failure this whole layer exists to remove.
        """
        if self.truncated:
            return DERIVATION_PARTIAL
        if self.page_count is not None and len(self.pages) < self.page_count:
            return DERIVATION_PARTIAL
        return DERIVATION_COMPLETE

    def full_text(self) -> str:
        """The document text, reconstructed. Offsets index into exactly this."""
        return PAGE_JOIN.join(page.text for page in self.pages)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def build_parsed_document(
    extraction: Any, *, max_pages: int = 0
) -> ParsedDocument | None:
    """Build the parsed structure from one extraction. Pure; never raises.

    Returns ``None`` when the extraction carries neither captured blocks nor
    tables — there is nothing to persist, and an empty derivation would assert
    that the document was parsed and found to contain nothing, which is different
    from not having been parsed.

    ``max_pages`` > 0 truncates and marks the result ``partial``; 0 means every
    page the extractor actually opened, which is already bounded upstream by
    ``primary_document_max_pdf_pages`` plus the supplemental pass.
    """
    if extraction is None:
        return None
    blocks = list(getattr(extraction, "blocks", None) or [])
    raw_tables = list(getattr(extraction, "tables", None) or [])
    if not blocks and not raw_tables:
        return None

    method = _clip(getattr(extraction, "extraction_method", None), _METHOD_MAX) or ""
    # HTML blocks carry no page number because HTML has no pages. Everything is
    # recorded under one container page and ``paginated`` says so.
    paginated = any(getattr(b, "page_number", None) is not None for b in blocks)

    parsed = ParsedDocument(
        pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
        extraction_method=method,
        paginated=paginated,
        page_count=getattr(extraction, "page_count", None),
        truncated=bool(getattr(extraction, "truncated", False)),
        language=_clip(getattr(extraction, "language", None), _LANGUAGE_MAX),
    )

    _build_pages(parsed, blocks, max_pages=max_pages)
    _build_sections(parsed, blocks)
    _build_tables(parsed, raw_tables)
    parsed.char_count = sum(len(p.text) for p in parsed.pages)
    return parsed


def _page_number_of(block: Any) -> int:
    value = getattr(block, "page_number", None)
    try:
        return int(value) if value is not None else UNPAGINATED_PAGE_NUMBER
    except (TypeError, ValueError):
        return UNPAGINATED_PAGE_NUMBER


def _build_pages(parsed: ParsedDocument, blocks: "Sequence[Any]", *, max_pages: int) -> None:
    """Group blocks into pages, in document order, recording exact offsets.

    Blocks arrive in the order the extractor produced them, which is document
    order within a page and page order across pages (including the targeted
    supplemental pass, which appends pages beyond the leading window). Grouping by
    "run of the same page number" rather than by sorting keeps that order intact —
    sorting would silently reorder a supplemental page into the middle and break
    the offsets' correspondence to the reading sequence.
    """
    if not blocks:
        return
    texts: dict[int, list[str]] = {}
    order: list[int] = []
    for block in blocks:
        page_no = _page_number_of(block)
        if page_no not in texts:
            texts[page_no] = []
            order.append(page_no)
        text = getattr(block, "text", "") or ""
        if text:
            texts[page_no].append(text)

    limit = int(max_pages) if max_pages and max_pages > 0 else len(order)
    if limit < len(order):
        parsed.truncated = True
    cursor = 0
    for index, page_no in enumerate(order[:limit]):
        body = "\n\n".join(texts[page_no])
        start = cursor
        end = start + len(body)
        parsed.pages.append(
            ParsedPage(page_number=page_no, text=body, char_start=start, char_end=end)
        )
        # The next page begins after this one plus the join character, exactly as
        # ``full_text`` will lay them out.
        cursor = end + (len(PAGE_JOIN) if index + 1 < min(limit, len(order)) else 0)


def _build_sections(parsed: ParsedDocument, blocks: "Sequence[Any]") -> None:
    """One section per run of consecutive blocks sharing a heading context."""
    if not blocks or not parsed.pages:
        return
    page_offsets = {p.page_number: p for p in parsed.pages}
    # Re-walk the blocks accumulating an offset per page, so a section's span is
    # expressed in the same coordinate system the pages are.
    running: dict[int, int] = {p.page_number: p.char_start for p in parsed.pages}

    current_key: tuple[str | None, str | None] | None = None
    current_start = 0
    current_end = 0
    current_pages: list[int] = []
    index = 0

    def _flush() -> None:
        nonlocal index
        if current_key is None:
            return
        ancestor, heading = current_key
        path = " > ".join([p for p in (ancestor, heading) if p])
        # A heading is only a scope signal when it uses scope VOCABULARY. Running
        # a raw heading through ``parse_scope`` — whose fail-closed rule is
        # "anything non-empty is a named segment" — labelled a real Pandora annual
        # report's "CONTENTS", "BIG" and "PICTURE" cover headings as business
        # segments, which is a false segment attribution on about half the
        # document. ``infer_heading_scope`` is the same vetting the extractor
        # already applies before it puts a scope on a table, and it returns None
        # when a heading carries no signal — unknown stays unknown.
        scope = parse_scope(infer_heading_scope(heading, ancestor))
        cols = scope_columns(scope)
        parsed.sections.append(
            ParsedSection(
                section_index=index,
                heading=_clip(heading, _HEADING_MAX),
                heading_path=_clip(path, _HEADING_PATH_MAX),
                page_start=min(current_pages) if current_pages else None,
                page_end=max(current_pages) if current_pages else None,
                char_start=current_start,
                char_end=current_end,
                scope_type=cols["scope_type"],
                scope_name=cols["scope_name"],
                scope_key=cols["scope_key"],
            )
        )
        index += 1

    for block in blocks:
        page_no = _page_number_of(block)
        if page_no not in page_offsets:
            # A page beyond the persisted limit: its blocks are not addressable in
            # this derivation's coordinate system, so they start no section.
            continue
        text = getattr(block, "text", "") or ""
        start = running[page_no]
        end = start + len(text)
        # Blocks within a page were joined with a blank line by ``_build_pages``.
        running[page_no] = end + 2
        key = (
            _clip(getattr(block, "ancestor_heading", None), _HEADING_MAX),
            _clip(getattr(block, "section", None), _HEADING_MAX),
        )
        if key != current_key:
            _flush()
            current_key = key
            current_start = start
            current_pages = []
        current_end = end
        if page_no not in current_pages:
            current_pages.append(page_no)
    _flush()


def _build_tables(parsed: ParsedDocument, raw_tables: "Sequence[Any]") -> None:
    """Keep every table as a GRID. Flattening is what destroys a column→year map."""
    for index, table in enumerate(raw_tables):
        rows = [list(map(str, row)) for row in (getattr(table, "rows", None) or [])]
        if not rows:
            continue
        scope = parse_scope(getattr(table, "scope", None))
        cols = scope_columns(scope)
        location = (
            _clip(getattr(table, "table_location", None), _TABLE_LOCATION_MAX)
            or f"t{index}"
        )
        parsed.tables.append(
            ParsedTable(
                table_index=index,
                table_location=location,
                page_number=getattr(table, "page_number", None),
                rows=rows,
                row_count=int(getattr(table, "row_count", 0) or len(rows)),
                col_count=int(
                    getattr(table, "col_count", 0) or max(len(r) for r in rows)
                ),
                reconstructed=bool(getattr(table, "reconstructed", False)),
                column_periods=[
                    str(p) for p in (getattr(table, "column_periods", None) or [])
                ],
                scope_type=cols["scope_type"],
                scope_name=cols["scope_name"],
                scope_key=cols["scope_key"],
                extraction_method=_clip(
                    getattr(table, "extraction_method", None), _METHOD_MAX
                )
                or parsed.extraction_method,
                confidence=getattr(table, "confidence", None),
            )
        )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


@dataclass
class DerivationResult:
    """Secret-free counts of what the parsed-representation writer did."""

    derivations_created: int = 0
    derivations_reused: int = 0
    pages_written: int = 0
    sections_written: int = 0
    tables_written: int = 0
    superseded: int = 0


async def persist_parsed_document(
    session: "Any",
    *,
    version_id: uuid.UUID,
    parsed: ParsedDocument | None,
    cfg: "Settings",
    now: "datetime | None" = None,
    result: DerivationResult | None = None,
) -> ResearchDocumentDerivation | None:
    """Write one derivation and its pages/sections/tables. Flush-only.

    Idempotent on ``(version, pipeline_version)``: re-parsing the same document
    with the same parser returns the existing derivation untouched rather than
    accumulating identical copies.

    A derivation under a NEWER pipeline version supersedes the active one — the
    old rows and their pages are kept, only ``is_active`` moves. An OLDER one is
    written but never activated, because reprocessing under a stale parser must
    not roll the corpus backwards.
    """
    counts = result if result is not None else DerivationResult()
    if not getattr(cfg, "v3_corpus_enabled", False) or parsed is None:
        return None
    if not parsed.has_content:
        return None

    existing = (
        await session.execute(
            select(ResearchDocumentDerivation)
            .where(
                ResearchDocumentDerivation.research_document_version_id == version_id,
                ResearchDocumentDerivation.pipeline_version == parsed.pipeline_version,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        counts.derivations_reused += 1
        return existing

    derivation = ResearchDocumentDerivation(
        id=uuid.uuid4(),
        research_document_version_id=version_id,
        pipeline_version=parsed.pipeline_version,
        extraction_method=parsed.extraction_method,
        status=parsed.status,
        is_active=False,
        pages_persisted=len(parsed.pages),
        page_count=parsed.page_count,
        paginated=parsed.paginated,
        char_count=parsed.char_count,
        section_count=len(parsed.sections),
        table_count=len(parsed.tables),
        truncated=parsed.truncated,
        language=parsed.language,
    )
    session.add(derivation)
    counts.derivations_created += 1
    await session.flush()

    for page in parsed.pages:
        session.add(
            ResearchDocumentPage(
                id=uuid.uuid4(),
                derivation_id=derivation.id,
                page_number=page.page_number,
                text=page.text,
                char_start=page.char_start,
                char_end=page.char_end,
                extraction_method=parsed.extraction_method,
            )
        )
    counts.pages_written += len(parsed.pages)

    for section in parsed.sections:
        session.add(
            ResearchDocumentSection(
                id=uuid.uuid4(),
                derivation_id=derivation.id,
                section_index=section.section_index,
                heading=section.heading,
                heading_path=section.heading_path,
                page_start=section.page_start,
                page_end=section.page_end,
                char_start=section.char_start,
                char_end=section.char_end,
                scope_type=section.scope_type,
                scope_name=section.scope_name,
                scope_key=section.scope_key,
            )
        )
    counts.sections_written += len(parsed.sections)

    for table in parsed.tables:
        session.add(
            ResearchDocumentTable(
                id=uuid.uuid4(),
                derivation_id=derivation.id,
                table_index=table.table_index,
                table_location=table.table_location,
                page_number=table.page_number,
                rows_json=table.rows,
                row_count=table.row_count,
                col_count=table.col_count,
                reconstructed=table.reconstructed,
                column_periods=table.column_periods or None,
                scope_type=table.scope_type,
                scope_name=table.scope_name,
                scope_key=table.scope_key,
                extraction_method=table.extraction_method,
                confidence=table.confidence,
            )
        )
    counts.tables_written += len(parsed.tables)
    await session.flush()

    await _activate_if_newer(session, derivation=derivation, result=counts)
    return derivation


async def _activate_if_newer(
    session: "Any",
    *,
    derivation: ResearchDocumentDerivation,
    result: DerivationResult,
) -> None:
    """Promote ``derivation`` when its parser is newer than the incumbent's.

    The demotion is flushed BEFORE the promotion: the partial unique index means
    two active derivations cannot coexist for even one statement, and SQLAlchemy
    would otherwise order the INSERT before the UPDATE.
    """
    current = (
        await session.execute(
            select(ResearchDocumentDerivation)
            .where(
                ResearchDocumentDerivation.research_document_version_id
                == derivation.research_document_version_id,
                ResearchDocumentDerivation.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if current is not None:
        if current.pipeline_version >= derivation.pipeline_version:
            # Reprocessing under a stale parser must not roll the corpus back.
            return
        current.is_active = False
        current.superseded_at = derivation.created_at
        result.superseded += 1
        await session.flush()

    derivation.is_active = True
    derivation.superseded_at = None
    await session.flush()


async def load_full_text(
    session: "Any", *, derivation_id: uuid.UUID
) -> str:
    """Reconstruct a derivation's full text from its pages, in document order."""
    pages = (
        (
            await session.execute(
                select(ResearchDocumentPage)
                .where(ResearchDocumentPage.derivation_id == derivation_id)
                .order_by(ResearchDocumentPage.char_start)
            )
        )
        .scalars()
        .all()
    )
    return PAGE_JOIN.join(p.text for p in pages)


__all__ = [
    "PAGE_JOIN",
    "UNPAGINATED_PAGE_NUMBER",
    "DerivationResult",
    "ParsedDocument",
    "ParsedPage",
    "ParsedSection",
    "ParsedTable",
    "build_parsed_document",
    "load_full_text",
    "persist_parsed_document",
]
