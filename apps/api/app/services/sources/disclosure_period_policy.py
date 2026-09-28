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

from datetime import date
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


def _began_by(period_type: str | None, year: int | None, ordinal: int | None,
              published: date | None, *, fiscal: bool = False) -> bool:
    if published is None or year is None:
        return True
    month = 1
    if period_type == "half" and ordinal:
        month = 1 + 6 * (int(ordinal) - 1)
    elif period_type == "quarter" and ordinal:
        month = 1 + 3 * (int(ordinal) - 1)
    # A FISCAL label names the year a fiscal year ENDS in: "Q1 FY2027" of a June
    # year-end began in July 2026. The fiscal year-end is not known here, so a fiscal
    # period may start up to twelve months before its calendar reading.
    start_year = int(year) - 1 if fiscal else int(year)
    return date(start_year, month, 1) <= published


def period_began_by(period_key: str, published: date | None, *,
                    basis: str | None = None) -> bool:
    """True unless the period demonstrably STARTS after ``published``.

    A period that had not even started when the document was published is a forecast
    ("Q1 2028 first production update", published 2026). Unknown publication, or a
    period this cannot place, is not treated as a forecast.
    """
    if published is None:
        return True
    from app.services.sources.financial_period import parse_period

    period = parse_period(period_key)
    return _began_by(period.period_type, period.year, period.ordinal, published,
                     fiscal=_is_fiscal(basis))


def _is_fiscal(basis: str | None) -> bool:
    from app.services.sources.document_period import BASIS_FISCAL_LABEL

    return basis == BASIS_FISCAL_LABEL


def document_period_for(
    *, title: str | None, url: str | None, extraction: Any, title_only: bool,
    published_at: date | None = None,
) -> Any:
    """``document_period_of`` honouring the policy: title only → no URL, no body, and
    no period that had not begun at publication (the facts' default period follows
    the same rule as the corpus version)."""
    from app.services.sources.document_period import (
        UNKNOWN_DOCUMENT_PERIOD,
        document_period_of,
    )

    if not title_only:
        return document_period_of(title=title, url=url, extraction=extraction)
    found = document_period_of(title=title, url=None, extraction=None)
    period = found.period
    if found.is_known and not _began_by(period.period_type, period.year, period.ordinal,
                                        published_at, fiscal=_is_fiscal(found.basis)):
        return UNKNOWN_DOCUMENT_PERIOD
    return found


__all__ = [
    "PERIOD_POLICY_TITLE_ONLY",
    "SOURCE_TYPE_ASX_ANNOUNCEMENT",
    "SOURCE_TYPE_UK_NSM_DISCLOSURE",
    "TITLE_ONLY_SOURCE_TYPES",
    "document_period_for",
    "is_title_only",
    "period_began_by",
]
