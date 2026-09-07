"""Document-aware chunking — V3.1 Slice 1.5.

WHAT MAKES A GOOD CHUNK IN THIS DOMAIN
======================================
A retrieval unit has one job: be the thing you put in front of a reader as "here
is where this came from". That rules out the two obvious answers.

*A fixed-width sliding window* cuts a sentence in half and, worse, cuts across a
heading. A window that starts in "Group results" and ends in "Jewellery Maisons"
carries two scopes and can be cited as either — a scope error baked into the
retrieval unit itself, which is the failure the whole period/scope apparatus
exists to prevent.

*A whole section* can run for twenty pages of an annual report. Retrieving it
answers "which section" when the question was "which sentence".

So the strategy here is structural first and size second:

1. **Never cross a section boundary.** A section is where the extractor's own
   heading structure says the subject changed, and it is where scope changes.
   This is the hard rule.
2. **Prefer to break at a page boundary**, once the chunk is already big enough
   to be worth citing. A chunk that sits on one page cites as "page 14"; one that
   straddles two cites as "pages 14-15", which is honest but less useful.
3. **Then break at a paragraph boundary**, never mid-sentence.
4. **Tables are never merged into prose.** A table gets its own chunk that points
   at the stored grid.

WHY THERE IS NO OVERLAP
=======================
Sliding-window chunkers overlap because fixed-width splitting cuts through
meaning, and overlap is the patch. This chunker splits at paragraph boundaries
inside a section, so a sentence is never cut in half and the patch is unnecessary.
Overlap would also cost something real: two chunks containing the same sentence
produce two hits for one piece of evidence and a citation that could name either.

CHUNK IDENTITY
==============
Stable, derived, and never assigned by an index:

    sha256(version_id | pipeline_version | profile | kind | ordinal | offsets | table_location)

Re-running the same parser over the same document version, under the same budget,
reproduces the same ids exactly — so reindexing puts the same span back in the
same slot and an existing citation keeps resolving. Re-parsing under a NEW
pipeline version, or under a different EXTRACTION PROFILE, deliberately produces
new ids: both are different readings of the document, and the old derivation and
its chunks are retained so the old citation still resolves too.

``extraction_profile`` earns its place in the key the hard way. Without it, a deep
reprocess of a real 169-page annual report collided on its first chunk: the deep
parse re-reads the same first pages, so its leading chunks have the same ordinal
and the same offsets as the live parse's and hashed to the same id. Two
derivations, one chunk id, and a UNIQUE violation that took a real document to
produce.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TypedDict

from app.models.research_chunk import CHUNK_KIND_PROSE, CHUNK_KIND_TABLE
from app.models.research_derivation import PROFILE_LIVE
from app.services.corpus.scope_resolution import (
    ScopeResolution,
    SegmentVocabulary,
    learn_segment_vocabulary,
    resolve_scope,
)
from app.services.sources.fact_scope import scope_from_columns

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.corpus.parsed import ParsedDocument, ParsedSection, ParsedTable

#: Chunk sizes, in characters rather than tokens: a character count is exact and
#: model-independent, and every token estimate is a different vendor's guess.
DEFAULT_TARGET_CHARS = 1_200
DEFAULT_MAX_CHARS = 2_000
#: Below this a chunk is not worth citing on its own, so a page boundary is not
#: taken as a break opportunity yet.
DEFAULT_MIN_CHARS = 300

_SECTION_PATH_MAX = 1000
_TABLE_LOCATION_MAX = 200
_CHUNK_ID_MAX = 80


@dataclass(frozen=True)
class BuiltChunk:
    """One chunk, before it touches a database or an index."""

    chunk_id: str
    kind: str
    ordinal: int
    text: str
    char_start: int
    char_end: int
    page_start: int | None = None
    page_end: int | None = None
    section_path: str | None = None
    table_location: str | None = None
    scope_type: str | None = None
    scope_name: str | None = None
    scope_key: str | None = None
    #: How the scope above was decided, and why it is absent when it is. Not persisted
    #: on the chunk row — it is diagnostic output for acceptance runs and review, and
    #: keeping it off the row avoids a migration for a value derivable from the parse.
    scope_method: str | None = None
    scope_confidence: float | None = None
    scope_evidence: str | None = None
    scope_ambiguity: str | None = None
    #: Index into ``ParsedDocument.tables`` for a table chunk; ``None`` for prose.
    table_index: int | None = None


def chunk_identity(
    *,
    research_document_version_id: uuid.UUID,
    pipeline_version: int,
    kind: str,
    ordinal: int,
    char_start: int,
    char_end: int,
    table_location: str | None = None,
    extraction_profile: str = PROFILE_LIVE,
) -> str:
    """The stable chunk id. Pure, and deliberately not a row id.

    A row id would be assigned by whichever database wrote it, so rebuilding the
    corpus would invalidate every citation. This is a function of the document's
    own coordinates, so it survives a rebuild.

    ``extraction_profile`` is part of the key because a deep re-read of the same
    document re-reads the same leading pages: without it, the deep derivation's
    first chunks share an ordinal and an offset range with the live one's and hash
    to the same id.
    """
    raw = "|".join(
        [
            str(research_document_version_id),
            str(int(pipeline_version)),
            extraction_profile or PROFILE_LIVE,
            kind,
            str(int(ordinal)),
            str(int(char_start)),
            str(int(char_end)),
            table_location or "",
        ]
    )
    return ("c:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40])[:_CHUNK_ID_MAX]


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _page_at(parsed: "ParsedDocument", offset: int) -> int | None:
    """The page a character offset falls on. ``None`` when it falls in no page."""
    for page in parsed.pages:
        if page.char_start <= offset < page.char_end or (
            page.char_start == page.char_end == offset
        ):
            return page.page_number
    return parsed.pages[-1].page_number if parsed.pages else None


def _page_breaks_within(parsed: "ParsedDocument", start: int, end: int) -> list[int]:
    """Offsets inside ``[start, end)`` where a new page begins."""
    return [p.char_start for p in parsed.pages if start < p.char_start < end]


def _paragraph_breaks(text: str, base: int) -> list[int]:
    """Absolute offsets of paragraph boundaries inside ``text``."""
    breaks: list[int] = []
    index = text.find("\n\n")
    while index != -1:
        breaks.append(base + index + 2)
        index = text.find("\n\n", index + 2)
    return breaks


def render_table(table: "ParsedTable", *, max_chars: int) -> str:
    """A searchable text surface for a grid — never a replacement for it.

    The grid stays in ``research_document_tables``; this exists so an agent can
    FIND it. The rendering deliberately leads with the column periods, because
    "revenue 32,549" is not evidence and "FY2025 revenue 32,549" is, and a
    retrieval hit is often read without opening the grid behind it.
    """
    lines: list[str] = []
    if table.column_periods:
        lines.append("Periods: " + ", ".join(table.column_periods))
    for row in table.rows:
        lines.append(" | ".join(str(cell) for cell in row))
    return "\n".join(lines)[:max_chars]


def build_chunks(
    parsed: "ParsedDocument",
    *,
    research_document_version_id: uuid.UUID,
    cfg: "Settings | None" = None,
) -> list[BuiltChunk]:
    """Split one parsed document into retrieval units. Pure; never raises.

    Prose chunks come first in document order, then one chunk per table. Tables
    trail because their offsets are not part of the prose coordinate system — a
    table's authoritative location is its ``table_location``, not a character
    span — and interleaving them would make ``ordinal`` mean two different things.
    """
    target = _setting(cfg, "v3_corpus_chunk_target_chars", DEFAULT_TARGET_CHARS)
    hard_max = max(target, _setting(cfg, "v3_corpus_chunk_max_chars", DEFAULT_MAX_CHARS))
    minimum = min(target, _setting(cfg, "v3_corpus_chunk_min_chars", DEFAULT_MIN_CHARS))

    full = parsed.full_text()
    chunks: list[BuiltChunk] = []
    ordinal = 0

    # The document's OWN reporting segments, learned before any chunk is scoped, so a
    # heading reading "Specialist Watchmakers" can be recognised as a segment of this
    # issuer without any issuer-specific rule. See ``scope_resolution``.
    vocabulary = _document_vocabulary(parsed, full)

    for section in parsed.sections:
        for span_index, (start, end) in enumerate(
            _split_section(parsed, section, full, target=target, hard_max=hard_max, minimum=minimum)
        ):
            text = full[start:end].strip()
            if not text:
                continue
            chunks.append(
                BuiltChunk(
                    chunk_id=chunk_identity(
                        research_document_version_id=research_document_version_id,
                        pipeline_version=parsed.pipeline_version,
                        extraction_profile=parsed.extraction_profile,
                        kind=CHUNK_KIND_PROSE,
                        ordinal=ordinal,
                        char_start=start,
                        char_end=end,
                    ),
                    kind=CHUNK_KIND_PROSE,
                    ordinal=ordinal,
                    text=text,
                    char_start=start,
                    char_end=end,
                    page_start=_page_at(parsed, start),
                    page_end=_page_at(parsed, max(start, end - 1)),
                    section_path=_clip(section.heading_path, _SECTION_PATH_MAX),
                    **_scope_fields(
                        resolve_scope(
                            text=text,
                            heading_scope=scope_from_columns(
                                section.scope_type, section.scope_name, section.scope_key
                            ),
                            section_path=section.heading_path,
                            heading_adjacent=span_index == 0,
                            vocabulary=vocabulary,
                        )
                    ),
                )
            )
            ordinal += 1

    for table in parsed.tables:
        text = render_table(table, max_chars=hard_max)
        if not text.strip():
            continue
        location = _clip(table.table_location, _TABLE_LOCATION_MAX)
        chunks.append(
            BuiltChunk(
                chunk_id=chunk_identity(
                    research_document_version_id=research_document_version_id,
                    pipeline_version=parsed.pipeline_version,
                    extraction_profile=parsed.extraction_profile,
                    kind=CHUNK_KIND_TABLE,
                    ordinal=ordinal,
                    char_start=0,
                    char_end=0,
                    table_location=location,
                ),
                kind=CHUNK_KIND_TABLE,
                ordinal=ordinal,
                text=text,
                # A table has no span in the prose coordinate system. Zeroes here
                # are honest: its provenance is the table location and the page.
                char_start=0,
                char_end=0,
                page_start=table.page_number,
                page_end=table.page_number,
                table_location=location,
                **_scope_fields(
                    resolve_scope(
                        text=text,
                        explicit=scope_from_columns(
                            table.scope_type, table.scope_name, table.scope_key
                        ),
                        table_caption=table.table_location,
                        table_rows=table.rows,
                        vocabulary=vocabulary,
                    )
                ),
                table_index=table.table_index,
            )
        )
        ordinal += 1

    return chunks


class _ScopeFields(TypedDict):
    """Exactly the ``BuiltChunk`` fields a resolution fills, so ``**`` stays typed."""

    scope_type: str | None
    scope_name: str | None
    scope_key: str | None
    scope_method: str | None
    scope_confidence: float | None
    scope_evidence: str | None
    scope_ambiguity: str | None


def _scope_fields(resolution: "ScopeResolution") -> _ScopeFields:
    """The chunk fields carrying a resolution and its provenance."""
    return {
        "scope_type": resolution.scope.scope_type,
        "scope_name": resolution.scope.scope_name,
        "scope_key": resolution.scope.scope_key,
        "scope_method": resolution.method,
        "scope_confidence": resolution.confidence,
        "scope_evidence": resolution.evidence,
        "scope_ambiguity": resolution.ambiguity,
    }


def _document_vocabulary(parsed: "ParsedDocument", full: str) -> SegmentVocabulary:
    """Learn this document's reporting segments from the document itself.

    Sections contribute their own text, so a "sales by business area" chart on a
    highlights page is a source; tables contribute their row labels when their caption
    says they are a segment disclosure. Nothing here knows any issuer's names.
    """
    section_texts: list[tuple[str | None, str]] = []
    for section in parsed.sections:
        body = full[section.char_start : section.char_end]
        if body.strip():
            section_texts.append((section.heading_path, body))
    table_rows = [(t.table_location, t.rows) for t in parsed.tables]
    return learn_segment_vocabulary(section_texts=section_texts, table_rows=table_rows)


def _split_section(
    parsed: "ParsedDocument",
    section: "ParsedSection",
    full: str,
    *,
    target: int,
    hard_max: int,
    minimum: int,
) -> "list[tuple[int, int]]":
    """Break one section into spans. Never crosses the section's own boundaries.

    Break opportunities, in the order they are preferred:
      1. a page boundary, once the chunk is already at least ``minimum`` long;
      2. a paragraph boundary;
      3. ``hard_max``, which is a bound on a pathological single paragraph rather
         than a normal split point.
    """
    start, end = section.char_start, section.char_end
    if end <= start:
        return []
    if end - start <= target:
        return [(start, end)]

    body = full[start:end]
    page_breaks = set(_page_breaks_within(parsed, start, end))
    opportunities = sorted(page_breaks | set(_paragraph_breaks(body, start)))

    spans: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > target:
        limit = min(end, cursor + hard_max)
        candidates = [o for o in opportunities if cursor < o <= limit]
        # A page boundary is preferred, but only once there is something worth
        # citing on this side of it — otherwise the first page of a section
        # produces a one-line chunk. Among the eligible ones, take whichever sits
        # closest to the target size.
        pages = [o for o in candidates if o in page_breaks and o - cursor >= minimum]
        if pages:
            chosen = min(pages, key=lambda o: abs((o - cursor) - target))
        elif candidates:
            # Otherwise a paragraph boundary: the last one that fits inside the
            # target, or failing that the first one past it — never mid-sentence.
            within = [o for o in candidates if o - cursor <= target]
            chosen = max(within) if within else min(candidates)
        elif end - cursor <= hard_max:
            # No break opportunity at all and the remainder fits: one long
            # paragraph is better kept whole than cut at an arbitrary character.
            break
        else:
            # A single paragraph longer than the hard maximum. Splitting on the
            # bound is the only option left, and it is bounding a pathological
            # document rather than expressing a chunking policy.
            chosen = limit
        if chosen <= cursor:
            break
        spans.append((cursor, chosen))
        cursor = chosen
    if cursor < end:
        spans.append((cursor, end))
    return spans


def _setting(cfg: "Settings | None", name: str, default: int) -> int:
    value = getattr(cfg, name, None) if cfg is not None else None
    try:
        resolved = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def filter_keys_for(
    *,
    company_id: uuid.UUID | None,
    document_type: str | None,
    source_tier: str | None,
    access_class: str | None,
    period_key: str | None,
    period_type: str | None,
    language: str | None,
    published_at: date | None,
    indexable: bool,
) -> dict[str, object]:
    """The denormalized filter columns every chunk of one version shares.

    Collected in one place so a new filter dimension is added once rather than at
    each of the three sites that build a chunk row.
    """
    return {
        "company_id": company_id,
        "document_type": document_type,
        "source_tier": source_tier,
        "access_class": access_class,
        "period_key": period_key,
        "period_type": period_type,
        "language": language,
        "published_at": published_at,
        "indexable": indexable,
    }


__all__ = [
    "CHUNK_KIND_PROSE",
    "CHUNK_KIND_TABLE",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_TARGET_CHARS",
    "BuiltChunk",
    "build_chunks",
    "chunk_identity",
    "filter_keys_for",
    "render_table",
]
