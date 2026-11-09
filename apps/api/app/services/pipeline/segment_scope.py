"""ADR-093 — the issuer's segment hierarchy, built ONCE per run from what the platform holds.

Read before any question is asked, from:

* the newest held primary documents that are periodic reports (their corpus chunks, in
  document order) — the issuer's own reportable-segment list, the segment sections of
  the filing, and the business lines each section states a revenue for;
* the business-segment members the issuer TAGGED in a held filing's inline XBRL
  (``inline_xbrl_facts``: active facts scoped to a business segment).

The chunk → segment-section map lets a finding citing a chunk of one segment's section
("Total nonperforming assets $5,496" in the CIB pages of a 10-Q) carry that segment's
scope, although SEC HTML filings parse with no sections.

Bounded, read in SAVEPOINTs, and never raising: ``None`` means unknown, and nothing is
scoped by an unknown hierarchy.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from typing import Any

from app.services.ledger.segment_hierarchy import (
    SegmentHierarchy,
    build_hierarchy,
    chunk_sections_of,
)

logger = logging.getLogger(__name__)

#: Periodic reports read for the hierarchy, newest first.
MAX_DOCUMENTS = 4
#: Chunks and characters read per document.
MAX_CHUNKS_PER_DOCUMENT = 4000
MAX_CHARS_PER_DOCUMENT = 2_500_000
#: Document types (or titles) that are periodic reports carrying segment sections.
_PERIODIC_RE = re.compile(
    r"\b(?:10-?k|10-?q|20-?f|40-?f|annual|interim|half[- ]?year|quarterly|semi-?annual)\b",
    re.IGNORECASE,
)


def periodic_versions(inventory: Sequence[dict[str, Any]] | None) -> list[uuid.UUID]:
    """The newest held periodic reports with chunks (``held_primary_documents`` rows)."""
    out: list[uuid.UUID] = []
    for row in inventory or ():
        if not isinstance(row, dict) or int(row.get("chunks") or 0) <= 0:
            continue
        label = f"{row.get('document_type') or ''} {row.get('title') or ''}".replace("_", " ")
        if not _PERIODIC_RE.search(label):
            continue
        try:
            out.append(uuid.UUID(str(row.get("version_id"))))
        except (TypeError, ValueError):
            continue
        if len(out) >= MAX_DOCUMENTS:
            break
    return out


async def _document_chunks(session: Any, version_id: uuid.UUID) -> list[tuple[str, int, str]]:
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk as C

    stmt = (
        select(C.chunk_id, C.char_start, C.text)
        .where(C.research_document_version_id == version_id)
        .order_by(C.ordinal)
        .limit(MAX_CHUNKS_PER_DOCUMENT)
    )
    async with session.begin_nested():
        rows = (await session.execute(stmt)).all()
    out: list[tuple[str, int, str]] = []
    total = 0
    for chunk_id, char_start, text in rows:
        total += len(text or "")
        if total > MAX_CHARS_PER_DOCUMENT:
            break
        out.append((str(chunk_id), int(char_start or 0), str(text or "")))
    return out


async def _tagged_segment_members(session: Any, company_id: uuid.UUID) -> list[str]:
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause
    from app.services.sources.inline_xbrl_facts import EXTRACTION_METHOD_INLINE_XBRL

    stmt = (
        select(ExtractedFact.scope_name)
        .join(ExtractedDocument, ExtractedFact.extracted_document_id == ExtractedDocument.id)
        .where(
            company_documents_clause(company_id),
            ExtractedFact.is_active.is_(True),
            ExtractedFact.scope_type == "segment",
            ExtractedFact.extraction_method == EXTRACTION_METHOD_INLINE_XBRL,
        )
        .distinct()
        .limit(200)
    )
    async with session.begin_nested():
        rows = (await session.execute(stmt)).scalars().all()
    return [str(r) for r in rows if r]


async def load_segment_hierarchy(
    session: Any,
    company_id: uuid.UUID | None,
    inventory: Sequence[dict[str, Any]] | None,
) -> SegmentHierarchy | None:
    """The issuer's segment hierarchy, or ``None`` (unknown). Never raises."""
    if session is None or company_id is None:
        return None
    try:
        documents: list[list[tuple[str, int, str]]] = []
        for version_id in periodic_versions(inventory):
            chunks = await _document_chunks(session, version_id)
            if chunks:
                documents.append(chunks)
        try:
            members = await _tagged_segment_members(session, company_id)
        except Exception:  # noqa: BLE001 - tagged members only confirm the list
            members = []
        texts = ["\n".join(text for _cid, _start, text in chunks) for chunks in documents]
        hierarchy = build_hierarchy(texts, xbrl_members=members)
        if hierarchy is None:
            return None
        sections: dict[str, str] = {}
        for chunks in documents:
            sections.update(chunk_sections_of(chunks, hierarchy.segments))
        return SegmentHierarchy(hierarchy.segments, hierarchy.business_lines,
                                hierarchy.basis, sections)
    except Exception:  # noqa: BLE001 - an unknown hierarchy scopes nothing
        logger.warning("segment hierarchy lookup failed")
        return None


__all__ = ["load_segment_hierarchy", "periodic_versions"]
