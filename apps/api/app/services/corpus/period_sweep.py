"""ADR-080 follow-up — clear forecast periods already stored on a company's corpus.

ADR-080 stops a forward date becoming a document's period at write time, and repairs a
filing when the SEC bridge meets it again (``filing_evidence.reconcile_filing_period``).
Neither reaches a row the run never re-encounters: the Cellectis pivot 6-K stored as
"2027-H2", another 6-K, an IR PDF or a web page read before ADR-080. So every research
run starts with this sweep over the subject's own corpus.

For each CURRENT version of the company's documents that carries a period and a
publication date, the period is cleared when it had not begun by that date, under the
same rule the corpus write path applies (``disclosure_period_policy.period_began_by``):
STRICT for a calendar basis. It is cleared on the version, on that version's chunks that
carry the same period, and on the document when the document carries that same period.
Nothing is ever FILLED here — a sweep has no source to fill from.

A FISCAL basis is never cleared (review of PR #323): "First Quarter Fiscal 2027" is
published in May 2026, and the sweep cannot tell a fiscal year's start from its key. A
``period_end_phrase`` row is read leniently, as fiscal, because before ADR-080 a stated
fiscal year ("first quarter of fiscal 2027") was stored under that basis; a genuinely
calendar forecast stored that way more than a year ahead is still cleared.

Bounded: the SELECT pre-filters in SQL to rows whose period year is not before their
publication year (a period that had not begun by its publication date always passes),
then pages through them by id, ``_BATCH`` at a time, reading at most
``MAX_SWEEP_VERSIONS``. The pages start at a RANDOM id and wrap around, so when a
company ever has more candidates than the cap, the rows past it are not starved by the
same settled rows on every run — each run reads another window, and every row is
eventually examined. Flush-only — the caller commits. Never raises.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, and_, cast, extract, func, or_, select, update

from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_document import ResearchDocument, ResearchDocumentVersion

logger = logging.getLogger(__name__)

#: Candidate versions read per sweep. Only rows whose period year is not before their
#: publication year are candidates, so a company's whole corpus is far below this; a
#: sweep that hits it reports ``capped``.
MAX_SWEEP_VERSIONS = 5_000
#: Rows per page, and so ids per ``IN`` list — under every backend's parameter limit.
_BATCH = 500


@dataclass
class PeriodSweepResult:
    """Secret-free counts, recorded on the run's outcome."""

    versions_examined: int = 0
    versions_cleared: int = 0
    chunks_cleared: int = 0
    documents_cleared: int = 0
    capped: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "versions_examined": self.versions_examined,
            "versions_cleared": self.versions_cleared,
            "chunks_cleared": self.chunks_cleared,
            "documents_cleared": self.documents_cleared,
        }
        if self.capped:
            out["capped"] = True
        if self.error:
            out["error"] = self.error
        return out


async def sweep_forecast_periods(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    limit: int = MAX_SWEEP_VERSIONS,
    start_after: uuid.UUID | None = None,
) -> PeriodSweepResult:
    """Clear every stored forecast period on ``company_id``'s current corpus versions.

    ``start_after`` is the id the pages start after (then wrap around); random by
    default — tests pass one to be deterministic."""
    from app.services.sources.disclosure_period_policy import period_began_by

    result = PeriodSweepResult()
    if company_id is None:
        return result
    try:
        # One SAVEPOINT around the read and the writes: on PostgreSQL a failed statement
        # aborts the shared transaction, and only a savepoint rollback un-aborts it.
        async with session.begin_nested():
            await _sweep(session, company_id=company_id, bound=max(1, int(limit)),
                         result=result, began_by=period_began_by,
                         start_after=start_after or uuid.uuid4())
        if result.versions_cleared:
            logger.info(
                "Forecast periods cleared for company %s: %d versions, %d chunks, "
                "%d documents", company_id, result.versions_cleared,
                result.chunks_cleared, result.documents_cleared,
            )
        return result
    except Exception as exc:  # noqa: BLE001 - a stale period must never end a run
        logger.warning("Forecast-period sweep failed for company %s", company_id)
        return PeriodSweepResult(
            versions_examined=result.versions_examined,
            error=type(exc).__name__,
        )


def _never_cleared_bases() -> tuple[str, ...]:
    from app.services.sources.document_period import (
        BASIS_FISCAL_LABEL,
        BASIS_FISCAL_PERIOD_END,
        BASIS_SEC_FISCAL_PERIOD_OF_REPORT,
    )

    return (BASIS_FISCAL_LABEL, BASIS_FISCAL_PERIOD_END, BASIS_SEC_FISCAL_PERIOD_OF_REPORT)


def _reading_basis(basis: str | None) -> str | None:
    """The basis the began-by rule reads a stored row under (see the module docstring)."""
    from app.services.sources.document_period import (
        BASIS_FISCAL_PERIOD_END,
        BASIS_PERIOD_END_PHRASE,
    )

    return BASIS_FISCAL_PERIOD_END if basis == BASIS_PERIOD_END_PHRASE else basis


async def _sweep(
    session: Any,
    *,
    company_id: uuid.UUID,
    bound: int,
    result: PeriodSweepResult,
    began_by: Callable[..., bool],
    start_after: uuid.UUID,
) -> None:
    """Page through the candidate rows by id from ``start_after``, wrapping around;
    clear each page's stale periods."""
    version = ResearchDocumentVersion
    never = _never_cleared_bases()
    base = (
        select(
            version.id,
            version.research_document_id,
            version.period_key,
            version.period_basis,
            version.published_at,
        )
        .join(ResearchDocument, ResearchDocument.id == version.research_document_id)
        .where(
            ResearchDocument.company_id == company_id,
            version.is_current.is_(True),
            version.period_key.is_not(None),
            version.published_at.is_not(None),
            or_(version.period_basis.is_(None), version.period_basis.not_in(never)),
            # Every key starts with its four-digit year ("2028-H2", "2027-Q1", "2026").
            # A period whose year is before the publication year began before it.
            func.substr(version.period_key, 1, 4)
            >= cast(extract("year", version.published_at), String),
        )
    )
    first, second = version.id > start_after, version.id <= start_after
    for window, later in ((first, second), (second, None)):
        cursor: uuid.UUID | None = None
        while True:
            if result.versions_examined >= bound:
                # Capped only when a candidate was left unread: past the cursor in this
                # window, or anywhere in the window still to come.
                unread = window if cursor is None else and_(window, version.id > cursor)
                if later is not None:
                    unread = or_(unread, later)
                left = (await session.execute(base.where(unread).limit(1))).first()
                result.capped = left is not None
                return
            page = base.where(window)
            if cursor is not None:
                page = page.where(version.id > cursor)
            size = min(_BATCH, bound - result.versions_examined)
            rows = (await session.execute(page.order_by(version.id).limit(size))).all()
            if not rows:
                break
            result.versions_examined += len(rows)
            cursor = rows[-1][0]
            await _clear(session, rows, result, began_by)
            if len(rows) < size:
                break


async def _clear(
    session: Any, rows: Any, result: PeriodSweepResult, began_by: Callable[..., bool]
) -> None:
    """Three UPDATEs per stale period on one page."""
    # Grouped by the stale key, so every UPDATE clears exactly that key and no other.
    stale: dict[str, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for version_id, document_id, key, basis, published in rows:
        if not began_by(key, published, basis=_reading_basis(basis)):
            stale.setdefault(key, []).append((version_id, document_id))

    for key, pairs in stale.items():
        version_ids = [v for v, _d in pairs]
        document_ids = list({d for _v, d in pairs})
        versions = await session.execute(
            update(ResearchDocumentVersion)
            .where(
                ResearchDocumentVersion.id.in_(version_ids),
                ResearchDocumentVersion.period_key == key,
            )
            .values(period_key=None, period_type=None, period_basis=None)
            .execution_options(synchronize_session="fetch")
        )
        chunks = await session.execute(
            update(ResearchDocumentChunk)
            .where(
                ResearchDocumentChunk.research_document_version_id.in_(version_ids),
                ResearchDocumentChunk.period_key == key,
            )
            .values(period_key=None, period_type=None)
            .execution_options(synchronize_session="fetch")
        )
        documents = await session.execute(
            update(ResearchDocument)
            .where(
                ResearchDocument.id.in_(document_ids),
                ResearchDocument.period_key == key,
            )
            .values(period_key=None, period_type=None)
            .execution_options(synchronize_session="fetch")
        )
        result.versions_cleared += int(versions.rowcount or 0)
        result.chunks_cleared += int(chunks.rowcount or 0)
        result.documents_cleared += int(documents.rowcount or 0)
    if stale:
        await session.flush()


__all__ = ["MAX_SWEEP_VERSIONS", "PeriodSweepResult", "sweep_forecast_periods"]
