"""Did this round of research actually acquire anything? V3.17.4.

WHY IMPROVEMENT IS NOT "MORE FINDINGS"
======================================
A Finding is **model output**. A threshold that rewards a model for emitting more of them
is a threshold that pays for prose, and — worse — an investigator defect would make
genuinely acquired evidence look like no improvement at all, terminating a loop that was
working. V3.16 is the reason the alternative exists: indexed chunk count is now a real,
moving number (MRNA went 0 → 383).

So improvement is measured on **what the platform retrieved and resolved**:

* indexed, searchable corpus chunks
* searchable document versions
* closable research gaps that closed
* active, canonically-scoped extracted facts

and Findings are carried as **secondary telemetry only** — reported, never decisive.

THE COUNTING TRAPS THIS MODULE IS SHAPED AROUND
===============================================
Every count below is scoped three ways, because each has burned this repository before:

1. **To the company.** Chunks carry a denormalised ``company_id`` *and* are joined
   through version → document, which is where ``company_id`` actually lives.
2. **To current/active rows only.** A superseded version and a deactivated derivation are
   history, not evidence. Counting them makes a re-extraction look like acquisition.
3. **To the right gap population.** ``research_gaps`` is the V3 research loop's gaps.
   ``DiscoveryCandidate.missing_fields_json`` is a different thing entirely and the two
   are not interchangeable.

A chunk must be BOTH ``indexable`` (a governance permission stamped at write time) and
carry ``indexed_at`` (index state). The lexical search filters on ``indexed_at IS NOT
NULL``, so a chunk with permission and no index entry is returned by nothing — which is
exactly the half of V3.16 that was invisible until a real PostgreSQL search ran.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class EvidenceSnapshot:
    """What this company's evidence looked like at one instant.

    Plain integers so it survives a JSONB round-trip unchanged — these are persisted on
    the decision as ``evidence_before_json`` / ``evidence_after_json`` and read back by a
    UI that must not recompute them differently.
    """

    indexed_chunks: int = 0
    searchable_documents: int = 0
    open_closable_gaps: int = 0
    active_facts: int = 0
    #: SECONDARY. Reported so a reader can see it; never decisive — see the module
    #: docstring.
    verified_findings: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "EvidenceSnapshot":
        """Rebuild from persisted JSON, tolerating a KEY that did not exist yet.

        An absent *key* reads as 0 because a snapshot is a measurement of a count, and a
        dimension that was not yet being measured contributes nothing to a delta on the
        dimensions that were.

        AN ABSENT *SNAPSHOT* IS A DIFFERENT THING ENTIRELY, and this method does not
        distinguish it — ``from_dict(None)`` returns all zeros, which reads as "this
        company had no evidence at all". That is exactly the substitution that produced
        `closable_gaps_closed: -14` in production: a baseline that was never taken was
        silently treated as a baseline of nothing, so evidence the platform had held for
        weeks was counted as this round's acquisition. Use :meth:`persisted` wherever the
        difference between "measured zero" and "never measured" can change a decision.
        """
        raw = raw or {}
        return cls(
            **{
                f: int(raw.get(f) or 0)
                for f in (
                    "indexed_chunks",
                    "searchable_documents",
                    "open_closable_gaps",
                    "active_facts",
                    "verified_findings",
                )
            }
        )

    @classmethod
    def persisted(cls, raw: dict[str, Any] | None) -> "EvidenceSnapshot | None":
        """The stored snapshot, or ``None`` when no snapshot was ever stored.

        The counterpart to :meth:`from_dict` for the one place the distinction is
        load-bearing: a **missing baseline is not a baseline of zero.** A decision whose
        ``evidence_before_json`` is NULL or ``{}`` was never measured, and a delta
        computed against nothing is not a small delta — it is not a delta at all.

        A snapshot of genuine zeros (``{"indexed_chunks": 0, ...}``) is a real
        measurement of a company that held no evidence yet, and is returned as such. The
        difference between that and ``None`` is the whole point of this method.
        """
        if not raw:
            return None
        return cls.from_dict(raw)


async def snapshot_evidence(
    session: AsyncSession, company_id: uuid.UUID
) -> EvidenceSnapshot:
    """Measure this company's evidence right now."""
    return EvidenceSnapshot(
        indexed_chunks=await _indexed_chunks(session, company_id),
        searchable_documents=await _searchable_documents(session, company_id),
        open_closable_gaps=await _open_closable_gaps(session, company_id),
        active_facts=await _active_facts(session, company_id),
        verified_findings=await _findings(session, company_id),
    )


async def _indexed_chunks(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Chunks a search can actually return.

    BOTH conditions, and the join to a current version. `indexable` is permission;
    `indexed_at` is index state; and a chunk hanging off a superseded version is not
    reachable evidence however well indexed it is.
    """
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    stmt = (
        select(func.count(ResearchDocumentChunk.id))
        .join(
            ResearchDocumentVersion,
            ResearchDocumentVersion.id
            == ResearchDocumentChunk.research_document_version_id,
        )
        .join(
            ResearchDocument,
            ResearchDocument.id == ResearchDocumentVersion.research_document_id,
        )
        .join(
            ResearchDocumentDerivation,
            ResearchDocumentDerivation.id == ResearchDocumentChunk.derivation_id,
        )
        .where(
            ResearchDocument.company_id == company_id,
            ResearchDocumentChunk.company_id == company_id,
            ResearchDocumentVersion.is_current.is_(True),
            # A superseded derivation is a previous extraction of the same version. Its
            # chunks are history, and counting them would make a re-extraction of a
            # document already held read as fresh acquisition.
            ResearchDocumentDerivation.is_active.is_(True),
            ResearchDocumentChunk.indexable.is_(True),
            ResearchDocumentChunk.indexed_at.is_not(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _searchable_documents(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Distinct current versions that carry at least one searchable chunk.

    Counting versions alone would count a document whose extraction produced nothing —
    which is precisely the false success V3.16 was opened to fix ("documents > 0" read as
    working while every search returned nought).
    """
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    stmt = (
        select(
            func.count(func.distinct(ResearchDocumentChunk.research_document_version_id))
        )
        .join(
            ResearchDocumentVersion,
            ResearchDocumentVersion.id
            == ResearchDocumentChunk.research_document_version_id,
        )
        .join(
            ResearchDocument,
            ResearchDocument.id == ResearchDocumentVersion.research_document_id,
        )
        .join(
            ResearchDocumentDerivation,
            ResearchDocumentDerivation.id == ResearchDocumentChunk.derivation_id,
        )
        .where(
            ResearchDocument.company_id == company_id,
            ResearchDocumentVersion.is_current.is_(True),
            ResearchDocumentDerivation.is_active.is_(True),
            ResearchDocumentChunk.indexable.is_(True),
            ResearchDocumentChunk.indexed_at.is_not(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _open_closable_gaps(session: AsyncSession, company_id: uuid.UUID) -> int:
    """V3 research-loop gaps that are open AND could still be closed by more work.

    ``closable=False`` is the platform saying "more research cannot fix this". Counting
    those would let an unclosable gap justify another paid round for ever.

    Scoped through ``ResearchRun.company_id`` because a gap belongs to a run, not to a
    company directly. **Not** ``DiscoveryCandidate.missing_fields_json``, which is a
    different population with a different meaning.
    """
    from app.models.ledger import ResearchGap, ResearchRun

    stmt = (
        select(func.count(ResearchGap.id))
        .join(ResearchRun, ResearchRun.id == ResearchGap.research_run_id)
        .where(
            ResearchRun.company_id == company_id,
            ResearchGap.closable.is_(True),
            ResearchGap.status == "open",
        )
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _active_facts(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Facts that are active AND carry a scope.

    ``is_active`` alone is not enough. A fact with ``scope_type=None`` cannot be attached
    to anything safely — that is precisely why MRNA's `cash_runway` is legitimately
    unsupported — so counting it as usable evidence would claim a capability the platform
    does not have.
    """
    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    stmt = (
        select(func.count(ExtractedFact.id))
        .join(
            ExtractedDocument,
            ExtractedDocument.id == ExtractedFact.extracted_document_id,
        )
        .where(
            ExtractedDocument.company_id == company_id,
            ExtractedFact.is_active.is_(True),
            ExtractedFact.scope_type.is_not(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _findings(session: AsyncSession, company_id: uuid.UUID) -> int:
    """Secondary telemetry. Reported, never decisive."""
    from app.models.ledger import ResearchFinding, ResearchRun

    stmt = (
        select(func.count(ResearchFinding.id))
        .join(ResearchRun, ResearchRun.id == ResearchFinding.research_run_id)
        .where(ResearchRun.company_id == company_id)
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


# --------------------------------------------------------------------------- #
# The verdict — pure, so every branch is testable without a database
# --------------------------------------------------------------------------- #

#: The dimensions that may justify another paid round. `verified_findings` is deliberately
#: NOT here: see the module docstring.
DECISIVE_DIMENSIONS: tuple[str, ...] = (
    "indexed_chunks_added",
    "searchable_documents_added",
    "closable_gaps_closed",
    "facts_added",
)


def measure_evidence_delta(
    before: EvidenceSnapshot, after: EvidenceSnapshot
) -> dict[str, Any]:
    """What changed, dimension by dimension, and whether that counts as improvement.

    Every dimension stays visible. Collapsing them into one opaque score would make the
    verdict unauditable — a reader could see "improved: false" and have no way to tell
    whether nothing was fetched or whether something was fetched and not indexed, which
    are different defects with different fixes.

    The rule is deliberately conservative: **at least one decisive dimension must have
    moved in the right direction.** Gaps CLOSING counts; gaps opening does not count
    against, because discovering a new gap is a legitimate result of having read
    something new.

    GAPS OPENED AND GAPS CLOSED ARE TWO NUMBERS, NOT ONE SIGNED NUMBER
    -----------------------------------------------------------------
    A single ``closable_gaps_closed`` that is allowed to go negative reads, to anything
    that renders it, as *"minus fourteen gaps were closed"* — a statement with no
    meaning. Production carried exactly that: `-14`, `-16`, `-28`, `-32`. So the two
    directions are reported separately and both are non-negative: gaps that closed, and
    gaps the round newly discovered. A reader can then see "closed 0, opened 14" and know
    what actually happened, which the signed number never said.
    """
    chunks = after.indexed_chunks - before.indexed_chunks
    documents = after.searchable_documents - before.searchable_documents
    # Gaps go the other way: fewer open closable gaps is progress. Split into two
    # non-negative counts — see the docstring.
    gap_movement = before.open_closable_gaps - after.open_closable_gaps
    gaps_closed = max(0, gap_movement)
    gaps_opened = max(0, -gap_movement)
    facts = after.active_facts - before.active_facts
    findings = after.verified_findings - before.verified_findings

    delta: dict[str, Any] = {
        "indexed_chunks_added": chunks,
        "searchable_documents_added": documents,
        "closable_gaps_closed": gaps_closed,
        #: Newly discovered gaps. Reported, never counted against improvement.
        "closable_gaps_opened": gaps_opened,
        "facts_added": facts,
        # Secondary. Present for the reader; absent from DECISIVE_DIMENSIONS.
        "verified_findings_added": findings,
        # This delta rests on a real before-snapshot. The controller writes
        # ``measurable: False`` instead of a delta when it does not have one, so a
        # reader is never handed zeros that were never measured.
        "measurable": True,
    }

    reasons: list[str] = []
    if chunks > 0:
        reasons.append(f"{chunks} new searchable corpus chunk(s)")
    if documents > 0:
        reasons.append(f"{documents} new searchable document version(s)")
    if gaps_closed > 0:
        reasons.append(f"{gaps_closed} closable research gap(s) closed")
    if facts > 0:
        reasons.append(f"{facts} new active, scoped fact(s)")
    if gaps_opened > 0:
        reasons.append(
            f"{gaps_opened} new closable research gap(s) were discovered — a legitimate "
            "result of having read something new, and not counted against improvement"
        )

    improved = any(delta[d] > 0 for d in DECISIVE_DIMENSIONS)
    if not improved:
        if findings > 0:
            # Worth saying out loud: the round produced model output and acquired
            # nothing. That is not improvement, and a reader needs to know the
            # difference rather than see a bare "false".
            reasons.append(
                f"{findings} finding(s) were written, but no new evidence was "
                "acquired — findings are model output and do not count as acquisition"
            )
        else:
            reasons.append("no evidence dimension moved")

    delta["improved"] = improved
    delta["reasons"] = reasons
    return delta


def unmeasurable_delta(reason: str) -> dict[str, Any]:
    """What is recorded when a round finished but its baseline was never taken.

    **No numeric dimensions at all.** Writing zeros here would be the original defect
    wearing a different hat: a reader — human or UI — cannot tell a measured zero from a
    zero that stands in for an absent measurement, and the whole point of this slice is
    that the platform stops making that substitution. ``improved`` is ``False`` because
    improvement was never established, not because it was disproved, and the reason says
    which.
    """
    return {
        "measurable": False,
        "improved": False,
        "reasons": [reason],
    }


__all__ = [
    "DECISIVE_DIMENSIONS",
    "EvidenceSnapshot",
    "measure_evidence_delta",
    "snapshot_evidence",
    "unmeasurable_delta",
]
