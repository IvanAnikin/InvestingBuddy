"""Does a held SEC filing still need its tagged statements read? — ADR-085.

A filing acquired before the inline-XBRL reader existed is searchable (the bridge's
state A) and is never fetched again, so its tagged statements would never be read. Its
raw bytes are not retained in production (``V3_ARTIFACT_STORE_BACKEND=none``), so they
cannot be re-parsed from storage either.

The decision: on re-encounter, such a filing is re-read through the existing bridge —
the same guarded SEC fetch, inside the caller's existing filing-body budget (it counts
as an attempt; with the budget spent, ``ready_only``, it is not re-read). A successful
re-read restamps the document at the current pipeline version, so the next run reuses
it free.

BOUNDED (ADR-085 review)
========================
* **The held document, not another one.** The acquirer is handed the held document's
  own file name, so a held 6-K exhibit is re-read — not the filing's cover page, which
  would leave the held row stale and due again on every run.
* **Once per pipeline version.** Whatever its outcome, the attempt is recorded as an
  ingestion attempt (``discovery_strategy`` = ``REREAD_STRATEGY``) for the company and
  the document's address; a recorded attempt is never repeated. A fetch that failed or
  a document that turned out to carry no tags therefore costs one budget slot, once.
  (Recording follows the ingestion-attempt gates; with them off, the next bound holds.)
* **At most one per run.** Whether a held document carries inline XBRL is not known
  before it is fetched, so at most ``MAX_REREADS_PER_SESSION`` re-read happens per
  database session — one research run's unit of work.

Only a form that can carry inline XBRL qualifies (10-K, 10-Q, 20-F, 40-F, 6-K and their
amendments), only an SEC Archives document, and only one stamped below
``INLINE_XBRL_MIN_PIPELINE_VERSION``. An 8-K or a non-SEC document is never re-read here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

IXBRL_CAPABLE_FORMS: frozenset[str] = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A"}
)
#: Re-reads per database session (one research run).
MAX_REREADS_PER_SESSION = 1
_SESSION_COUNTER_KEY = "adr085_held_filing_rereads"


def _reread_strategy() -> str:
    from app.services.sources.extraction_pipeline_version import (
        INLINE_XBRL_MIN_PIPELINE_VERSION,
    )

    return f"ixbrl_reread_v{INLINE_XBRL_MIN_PIPELINE_VERSION}"


@dataclass(frozen=True)
class HeldFilingReread:
    """A held filing document due one re-read."""

    extracted_document_id: uuid.UUID
    canonical_url: str
    #: The held document's own file name in the filing (``clls-ex99_1.htm``).
    document_name: str | None
    source_type: str
    source_tier: str


def _document_name(url: str) -> str | None:
    name = urlsplit(url).path.rstrip("/").rpartition("/")[2]
    return name or None


async def _held_document(
    session: Any, *, extracted_document_id: uuid.UUID | None, form: str | None
) -> Any | None:
    """The held document when it is a pre-reader SEC Archives filing of an iXBRL form."""
    if (form or "").strip().upper() not in IXBRL_CAPABLE_FORMS or extracted_document_id is None:
        return None
    from app.models.extracted_document import ExtractedDocument
    from app.services.sources.extraction_pipeline_version import (
        INLINE_XBRL_MIN_PIPELINE_VERSION,
    )
    from app.services.sources.inline_xbrl_facts import is_sec_archive_url

    document = await session.get(ExtractedDocument, extracted_document_id)
    if document is None or not is_sec_archive_url(document.canonical_url):
        return None
    if (document.pipeline_version or 0) >= INLINE_XBRL_MIN_PIPELINE_VERSION:
        return None
    return document


async def held_filing_needs_fact_reread(
    session: Any, *, extracted_document_id: uuid.UUID | None, form: str | None
) -> bool:
    """True when a held filing document predates the inline-XBRL reader. Never raises.

    The document test alone — ``plan_held_filing_reread`` adds the once-per-version and
    once-per-run bounds.
    """
    try:
        return await _held_document(
            session, extracted_document_id=extracted_document_id, form=form
        ) is not None
    except Exception:  # noqa: BLE001 - a held filing stays usable whatever this says
        logger.warning("Could not check held filing document %s", extracted_document_id)
        return False


def _session_rereads(session: Any) -> int:
    info = getattr(session, "info", None)
    return int(info.get(_SESSION_COUNTER_KEY, 0)) if isinstance(info, dict) else 0


def _count_session_reread(session: Any) -> None:
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info[_SESSION_COUNTER_KEY] = int(info.get(_SESSION_COUNTER_KEY, 0)) + 1


async def _already_attempted(session: Any, *, company_id: uuid.UUID, url: str) -> bool:
    from sqlalchemy import select

    from app.models.document_ingestion_attempt import DocumentIngestionAttempt
    from app.services.document_ingestion_attempt_service import url_hash_of

    found = (
        await session.execute(
            select(DocumentIngestionAttempt.id)
            .where(
                DocumentIngestionAttempt.company_id == company_id,
                DocumentIngestionAttempt.url_hash == url_hash_of(url),
                DocumentIngestionAttempt.discovery_strategy == _reread_strategy(),
            )
            .limit(1)
        )
    ).first()
    return found is not None


async def plan_held_filing_reread(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    extracted_document_id: uuid.UUID | None,
    form: str | None,
) -> HeldFilingReread | None:
    """The re-read a held filing is due, within every bound above, or ``None``.

    Counts the re-read against this session's bound when it returns one. Never raises.
    """
    if company_id is None:
        return None
    try:
        if _session_rereads(session) >= MAX_REREADS_PER_SESSION:
            return None
        document = await _held_document(
            session, extracted_document_id=extracted_document_id, form=form
        )
        if document is None:
            return None
        if await _already_attempted(
            session, company_id=company_id, url=document.canonical_url
        ):
            return None
    except Exception:  # noqa: BLE001 - a held filing stays usable whatever this says
        logger.warning("Could not check held filing document %s", extracted_document_id)
        return None
    _count_session_reread(session)
    return HeldFilingReread(
        extracted_document_id=document.id,
        canonical_url=document.canonical_url,
        document_name=_document_name(document.canonical_url),
        source_type=document.source_type or "",
        source_tier=document.source_tier or "",
    )


async def record_held_filing_reread(
    session: Any, *, company_id: uuid.UUID, plan: HeldFilingReread, cfg: Any
) -> None:
    """Record that ``plan`` was attempted, with its outcome. Never raises.

    The outcome is read from the held row itself: restamped at the reader's version
    means its tags were read; anything else is a failed re-read, recorded all the same
    so it is not repeated.
    """
    from app.models.document_ingestion_attempt import (
        STATUS_EXTRACTED,
        STATUS_EXTRACTION_FAILED,
    )
    from app.models.extracted_document import ExtractedDocument
    from app.services.document_ingestion_attempt_service import (
        IngestionAttemptRecord,
        record_ingestion_attempts,
    )
    from app.services.sources.extraction_pipeline_version import (
        INLINE_XBRL_MIN_PIPELINE_VERSION,
    )

    try:
        async with session.begin_nested():
            version = (
                await session.execute(
                    _version_query(ExtractedDocument, plan.extracted_document_id)
                )
            ).scalar_one_or_none()
            read = (version or 0) >= INLINE_XBRL_MIN_PIPELINE_VERSION
            await record_ingestion_attempts(
                session,
                company_id=company_id,
                agent_run_id=None,
                attempts=[
                    IngestionAttemptRecord(
                        canonical_url=plan.canonical_url,
                        source_type=plan.source_type,
                        source_tier=plan.source_tier,
                        status=STATUS_EXTRACTED if read else STATUS_EXTRACTION_FAILED,
                        discovery_strategy=_reread_strategy(),
                        failure_code=None if read else "unknown",
                    )
                ],
                cfg=cfg,
            )
    except Exception:  # noqa: BLE001 - a record of an attempt never fails the run
        logger.warning("Could not record the held-filing re-read %s", plan.extracted_document_id)


def _version_query(model: Any, document_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    return select(model.pipeline_version).where(model.id == document_id)


__all__ = [
    "IXBRL_CAPABLE_FORMS",
    "MAX_REREADS_PER_SESSION",
    "HeldFilingReread",
    "held_filing_needs_fact_reread",
    "plan_held_filing_reread",
    "record_held_filing_reread",
]
