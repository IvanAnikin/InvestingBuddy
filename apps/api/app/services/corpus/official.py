"""Reads of the company's OWN documents — open-web W5 review round 1 (F1 / C-H1).

The subject profile (which commodities the company sells) and the stage detector (is it a
pre-revenue developer) are inputs to the company web stage: they choose query templates.
They must therefore be computed from the company's official documents ONLY. Once the web
stage has stored a page for the company, that page's chunks carry ``company_id`` too, and
a plain ``WHERE company_id = …`` read would hand a third party's text to both detectors on
the next run — a page-to-query path (PI-07) through the back door.

A web document is exactly a version with ``web_extractor_version`` set (``ingest.py``
writes it for every open-web document, including a verified lead's). This module is the one
place that says so, so the two readers cannot drift.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def official_chunk_texts(company_id: Any, *, limit: int) -> Any:
    """A ``select`` of chunk text for ``company_id`` from NON-web documents only."""
    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V

    return (
        sa.select(C.text)
        .outerjoin(V, V.id == C.research_document_version_id)
        .where(C.company_id == company_id, V.web_extractor_version.is_(None))
        .limit(limit)
    )


__all__ = ["official_chunk_texts"]
