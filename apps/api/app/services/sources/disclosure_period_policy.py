"""Where an official announcement's reporting period may be read from — ONE rule.

An announcement states its own period in its official TITLE when it states one at all
("Interim results for the six months ended 31 December 2025"). Its BODY is where
forecasts live: measured on real text, the body detector stamped "first production
expected in Q1 2028" as the document's period, and a quarterly report for the quarter
ENDED 30 June 2026 as 2028-Q1 too.

So a document delivered as an official announcement takes its period from the title
only — on every path that computes one: live ingestion (corpus version and the facts'
default period), the reuse path and the backfill. The policy is keyed by the persisted
``source_type``, so it survives a round trip through the database instead of living
only on an in-memory artifact.
"""

from __future__ import annotations

from typing import Any

#: ``PrimaryDocumentArtifact.period_policy`` / ``DocumentVersionInput.period_policy``.
PERIOD_POLICY_TITLE_ONLY = "title_only"

#: ``ExtractedDocument.source_type`` values written for official announcements.
SOURCE_TYPE_UK_NSM_DISCLOSURE = "uk_nsm_disclosure"
SOURCE_TYPE_ASX_ANNOUNCEMENT = "asx_announcement"
TITLE_ONLY_SOURCE_TYPES: frozenset[str] = frozenset(
    {SOURCE_TYPE_UK_NSM_DISCLOSURE, SOURCE_TYPE_ASX_ANNOUNCEMENT}
)


def is_title_only(*, policy: str | None = None, source_type: str | None = None) -> bool:
    """True when the period must come from the official title alone."""
    return policy == PERIOD_POLICY_TITLE_ONLY or (source_type or "") in TITLE_ONLY_SOURCE_TYPES


def document_period_for(
    *, title: str | None, url: str | None, extraction: Any, title_only: bool
) -> Any:
    """``document_period_of`` honouring the policy: title only → no URL, no body."""
    from app.services.sources.document_period import document_period_of

    if title_only:
        return document_period_of(title=title, url=None, extraction=None)
    return document_period_of(title=title, url=url, extraction=extraction)


__all__ = [
    "PERIOD_POLICY_TITLE_ONLY",
    "SOURCE_TYPE_ASX_ANNOUNCEMENT",
    "SOURCE_TYPE_UK_NSM_DISCLOSURE",
    "TITLE_ONLY_SOURCE_TYPES",
    "document_period_for",
    "is_title_only",
]
