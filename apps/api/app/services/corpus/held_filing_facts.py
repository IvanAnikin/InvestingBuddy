"""Does a held SEC filing still need its tagged statements read? — ADR-085.

A filing acquired before the inline-XBRL reader existed is searchable (the bridge's
state A) and is never fetched again, so its tagged statements would never be read. Its
raw bytes are not retained in production (``V3_ARTIFACT_STORE_BACKEND=none``), so they
cannot be re-parsed from storage either.

The decision: on re-encounter, such a filing is re-read ONCE through the existing bridge
— the same guarded SEC fetch, inside the caller's existing filing-body budget (it counts
as an attempt; with the budget spent, ``ready_only``, it is not re-read). The re-read
restamps the document at the current pipeline version, so the next run reuses it free.

Only a form that can carry inline XBRL qualifies (10-K, 10-Q, 20-F, 40-F, 6-K and their
amendments), only an SEC Archives document, and only one stamped below
``INLINE_XBRL_MIN_PIPELINE_VERSION``. An 8-K or a non-SEC document is never re-read here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

IXBRL_CAPABLE_FORMS: frozenset[str] = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A"}
)


async def held_filing_needs_fact_reread(
    session: Any, *, extracted_document_id: uuid.UUID | None, form: str | None
) -> bool:
    """True when a held filing document predates the inline-XBRL reader. Never raises."""
    if (form or "").strip().upper() not in IXBRL_CAPABLE_FORMS or extracted_document_id is None:
        return False
    try:
        from app.models.extracted_document import ExtractedDocument
        from app.services.sources.extraction_pipeline_version import (
            INLINE_XBRL_MIN_PIPELINE_VERSION,
        )
        from app.services.sources.inline_xbrl_facts import is_sec_archive_url

        document = await session.get(ExtractedDocument, extracted_document_id)
        if document is None or not is_sec_archive_url(document.canonical_url):
            return False
        return (document.pipeline_version or 0) < INLINE_XBRL_MIN_PIPELINE_VERSION
    except Exception:  # noqa: BLE001 - a held filing stays usable whatever this says
        logger.warning("Could not check held filing document %s", extracted_document_id)
        return False


__all__ = ["IXBRL_CAPABLE_FORMS", "held_filing_needs_fact_reread"]
