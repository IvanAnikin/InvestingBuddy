"""ADR-080 follow-up — clear forecast periods already stored on a company's corpus.

ADR-080 stops a forward date becoming a document's period at write time, and repairs a
filing when the SEC bridge meets it again (``filing_evidence.reconcile_filing_period``).
Neither reaches a row the run never re-encounters: the Cellectis pivot 6-K stored as
"2027-H2", another 6-K, an IR PDF or a web page read before ADR-080. So every research
run starts with this sweep over the subject's own corpus.

For each CURRENT version of the company's documents that carries a period and a
publication date, the period is cleared when it had not begun by that date, under the
same rule the corpus write path applies (``disclosure_period_policy.period_began_by``):
STRICT for a calendar basis, lenient only for a fiscal one. It is cleared on the version,
on that version's chunks that carry the same period, and on the document when the
document carries that same period. Nothing is ever FILLED here — a sweep has no source to
fill from.

Bounded: at most ``MAX_SWEEP_VERSIONS`` versions are read in one SELECT, and the clearing
is a handful of UPDATE statements per distinct stale period (versions in batches of
``_BATCH``). Flush-only — the caller commits. Never raises.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_document import ResearchDocument, ResearchDocumentVersion

logger = logging.getLogger(__name__)

#: Versions read per sweep. A company's current corpus is hundreds of documents; a
#: sweep that hit this cap reports it, and the next run continues the work.
MAX_SWEEP_VERSIONS = 5_000
#: Ids per ``IN`` list, well under every backend's bound-parameter limit.
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
) -> PeriodSweepResult:
    """Clear every stored forecast period on ``company_id``'s current corpus versions."""
    from app.services.sources.disclosure_period_policy import period_began_by

    result = PeriodSweepResult()
    if company_id is None:
        return result
    try:
        # One SAVEPOINT around the read and the writes: on PostgreSQL a failed statement
        # aborts the shared transaction, and only a savepoint rollback un-aborts it.
        async with session.begin_nested():
            await _sweep(session, company_id=company_id, bound=max(1, int(limit)),
                         result=result, began_by=period_began_by)
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


async def _sweep(
    session: Any,
    *,
    company_id: uuid.UUID,
    bound: int,
    result: PeriodSweepResult,
    began_by: Callable[..., bool],
) -> None:
    """One bounded SELECT, then three UPDATEs per stale period and batch."""
    rows = (
        await session.execute(
            select(
                ResearchDocumentVersion.id,
                ResearchDocumentVersion.research_document_id,
                ResearchDocumentVersion.period_key,
                ResearchDocumentVersion.period_basis,
                ResearchDocumentVersion.published_at,
            )
            .join(
                ResearchDocument,
                ResearchDocument.id == ResearchDocumentVersion.research_document_id,
            )
            .where(
                ResearchDocument.company_id == company_id,
                ResearchDocumentVersion.is_current.is_(True),
                ResearchDocumentVersion.period_key.is_not(None),
                ResearchDocumentVersion.published_at.is_not(None),
            )
            .order_by(ResearchDocumentVersion.id)
            .limit(bound + 1)
        )
    ).all()
    if len(rows) > bound:
        result.capped = True
        rows = rows[:bound]
    result.versions_examined = len(rows)

    # Grouped by the stale key, so every UPDATE clears exactly that key and no other.
    stale: dict[str, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for version_id, document_id, key, basis, published in rows:
        if not began_by(key, published, basis=basis):
            stale.setdefault(key, []).append((version_id, document_id))

    for key, pairs in stale.items():
        for start in range(0, len(pairs), _BATCH):
            batch = pairs[start:start + _BATCH]
            version_ids = [v for v, _d in batch]
            document_ids = list({d for _v, d in batch})
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
