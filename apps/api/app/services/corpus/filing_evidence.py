"""The filing-to-corpus evidence bridge — V3.16.

WHAT WAS WRONG
==============
The biotech playbook's blocking ``pipeline_state`` question needs filing *content*, and
the platform had every piece needed to supply it except the join between them.

``search_company_corpus`` searches ``research_document_chunks`` and nothing else. Chunks
exist only when an extraction captured **blocks or tables**. Two paths produce a document
that has none, and both of them look like success from the outside:

* ``backfill_from_extracted_documents`` calls only ``upsert_document_version``. It
  creates documents and versions and **zero** derivations, pages or chunks. A backfill
  can report "documents created" and leave every search at nought.
* ``load_reusable_documents`` rebuilds a cached artifact from *bounded excerpts*, not
  blocks. So a cache hit inside the reuse TTL creates a version with no chunks — and the
  reuse is precisely what skips the re-extraction that would have created them. The
  document stays chunkless for as long as the cache keeps answering.

The shared mistake is treating **a row's existence as searchability**. This module makes
that impossible to repeat by giving the codebase exactly one definition of ready.

WHAT READY MEANS — THE ONLY DEFINITION
======================================
A filing is corpus-search ready for a company when all four hold together:

1. a ``ResearchDocumentVersion`` for **that company**, ``is_current``;
2. carrying an ``is_active`` ``ResearchDocumentDerivation``;
3. with at least one ``ResearchDocumentChunk``;
4. and that chunk both ``indexable`` **and** carrying ``indexed_at``.

Condition 4 is two conditions because the corpus has two independent failure modes, and
the second was invisible until a real PostgreSQL search ran against real rows.
``indexable`` is a *governance* permission stamped at write time. ``indexed_at`` is the
*index state*, and the lexical query filters on ``indexed_at IS NOT NULL`` — so a chunk
that is permitted to be indexed and never was is as unfindable as one that does not
exist. Only ``index_version`` sets it, and on the live ingestion path nothing called
``index_version`` at all: chunks were created by ``persist_chunks`` and left un-indexed
for ever.

Nothing else counts, and no caller is allowed its own opinion. ``if document exists``
scattered across three callers is how the original defect survived review.

DISCOVERY IS NOT EVIDENCE
=========================
``get_recent_filings`` stays what it is: regulator metadata. This module never turns
metadata into a claim about what a filing *says*. It either produces real indexed content
that a specialist can retrieve and cite, or it returns an honest reason why it could not —
never a state in between, and never a citation to a document nobody read.

THE MODEL CANNOT POINT THIS ANYWHERE
====================================
``ensure_filing_corpus_evidence`` takes a company, a CIK, an accession and a form. It
takes **no URL**. Every URL is built inside the existing SEC pipeline from code-defined
constants, and the accession is refused unless it is exactly eighteen digits. A model can
influence *which of the issuer's own filings* is fetched; it cannot influence *what host
or path* is fetched, because it never supplies one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import func, select

from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_derivation import ResearchDocumentDerivation
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.sources.sec_filing_documents import (
    format_accession,
    normalize_accession,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

logger = logging.getLogger(__name__)

#: The four states a requested filing can be in. Named because the difference between
#: B and A is the entire subject of this module, and a boolean would hide it.
STATE_READY = "ready"
STATE_HISTORICAL_WITHOUT_CHUNKS = "historical_without_chunks"
STATE_ABSENT = "absent"
STATE_UNAVAILABLE = "unavailable"


def canonical_accession(raw: str | int | None) -> str | None:
    """The canonical dashed accession (``0001682852-25-000006``), or ``None``.

    Identity for a filing is its accession, not its title and not a URL string. Two
    retrievals of one filing can differ in both of those; the accession is the
    regulator's own key, and an amendment carries a *different* one — which is exactly
    what stops a 10-K/A from silently satisfying a request for the 10-K.
    """
    if raw is None:
        return None
    normalized = normalize_accession(str(raw))
    if normalized is None:
        return None
    return format_accession(normalized)


def _url_fragment(accession_canonical: str) -> str:
    """The dash-free accession as it appears as a path segment on SEC Archives."""
    return accession_canonical.replace("-", "")


# ---------------------------------------------------------------------------
# Readiness — one definition
# ---------------------------------------------------------------------------


@dataclass
class FilingEvidenceState:
    """What the platform currently holds for one filing, for one company."""

    state: str
    accession: str | None = None
    version_id: uuid.UUID | None = None
    derivation_id: uuid.UUID | None = None
    extracted_document_id: uuid.UUID | None = None
    chunk_count: int = 0
    indexable_chunk_count: int = 0
    detail: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.state == STATE_READY


async def filing_evidence_state(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    accession: str | int | None,
) -> FilingEvidenceState:
    """Classify one filing into A / B / C without touching the network.

    Answerable from the accession alone, which is what makes the repeat-run case free:
    a second request for a filing already indexed resolves here and stops.

    **Company-scoped throughout.** A version is matched only when its ``company_id``
    equals the requested company, so a document filed by, co-filed with, or mirrored for
    another issuer can never make this one look ready — content hashes and URLs coincide
    far too easily for either to carry company identity.
    """
    canonical = canonical_accession(accession)
    if canonical is None:
        return FilingEvidenceState(
            state=STATE_ABSENT, detail="accession could not be normalised"
        )
    if company_id is None:
        return FilingEvidenceState(
            state=STATE_ABSENT, accession=canonical, detail="no company identity"
        )

    fragment = _url_fragment(canonical)
    # The company lives on the LOGICAL document, not the retrieval, so the scope filter
    # is a join rather than a column on the version. Getting that wrong is precisely how
    # one issuer's filing would come to satisfy another's request.
    version = (
        await session.execute(
            select(ResearchDocumentVersion)
            .join(
                ResearchDocument,
                ResearchDocument.id == ResearchDocumentVersion.research_document_id,
            )
            .where(
                ResearchDocument.company_id == company_id,
                ResearchDocumentVersion.canonical_url.contains(fragment),
            )
            .order_by(ResearchDocumentVersion.is_current.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if version is None:
        # No corpus version. There may still be a V2 row — that is STATE B, and the
        # caller must not mistake its excerpts for searchable content.
        extracted_id = await _historical_extracted_document_id(
            session, company_id=company_id, fragment=fragment
        )
        if extracted_id is not None:
            return FilingEvidenceState(
                state=STATE_HISTORICAL_WITHOUT_CHUNKS,
                accession=canonical,
                extracted_document_id=extracted_id,
                detail=(
                    "a V2 extracted document exists with bounded excerpts and no corpus "
                    "version; excerpts are not searchable filing content"
                ),
            )
        return FilingEvidenceState(state=STATE_ABSENT, accession=canonical)

    derivation = (
        await session.execute(
            select(ResearchDocumentDerivation)
            .where(
                ResearchDocumentDerivation.research_document_version_id == version.id,
                ResearchDocumentDerivation.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    chunk_count = 0
    indexable = 0
    if derivation is not None:
        chunk_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ResearchDocumentChunk)
                    .where(ResearchDocumentChunk.derivation_id == derivation.id)
                )
            ).scalar_one()
            or 0
        )
        # BOTH conditions. `indexable` is permission; `indexed_at` is index state, and
        # the lexical query filters on the latter. A chunk with permission and no index
        # entry is returned by nothing.
        indexable = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ResearchDocumentChunk)
                    .where(
                        ResearchDocumentChunk.derivation_id == derivation.id,
                        ResearchDocumentChunk.indexable.is_(True),
                        ResearchDocumentChunk.indexed_at.is_not(None),
                    )
                )
            ).scalar_one()
            or 0
        )

    if derivation is not None and indexable > 0:
        return FilingEvidenceState(
            state=STATE_READY,
            accession=canonical,
            version_id=version.id,
            derivation_id=derivation.id,
            extracted_document_id=version.extracted_document_id,
            chunk_count=chunk_count,
            indexable_chunk_count=indexable,
        )

    # A version with no active derivation, or one whose chunks are absent or all
    # non-indexable. This is the state the backfill leaves behind, and the state a
    # cache hit leaves behind. It is NOT evidence.
    return FilingEvidenceState(
        state=STATE_HISTORICAL_WITHOUT_CHUNKS,
        accession=canonical,
        version_id=version.id,
        derivation_id=derivation.id if derivation is not None else None,
        extracted_document_id=version.extracted_document_id,
        chunk_count=chunk_count,
        indexable_chunk_count=indexable,
        detail=(
            f"a corpus version exists with {chunk_count} chunk(s) and "
            f"{indexable} of them indexed; the retrieval backend can return nothing "
            "for it"
        ),
    )


async def _historical_extracted_document_id(
    session: Any, *, company_id: uuid.UUID, fragment: str
) -> uuid.UUID | None:
    """The V2 ``ExtractedDocument`` for this filing, if this company has one."""
    from app.models.extracted_document import ExtractedDocument

    return (
        await session.execute(
            select(ExtractedDocument.id)
            .where(
                ExtractedDocument.company_id == company_id,
                ExtractedDocument.canonical_url.contains(fragment),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def is_corpus_search_ready(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    accession: str | int | None,
) -> bool:
    """Can the retrieval backend actually return content for this filing?

    The single predicate. Callers ask this; they do not re-implement it.
    """
    state = await filing_evidence_state(
        session, company_id=company_id, accession=accession
    )
    return state.is_ready


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


class FilingAcquirer(Protocol):
    """Fetch + extract + persist + index one filing. Injected so tests stay offline."""

    async def __call__(
        self,
        session: Any,
        *,
        company_id: uuid.UUID,
        cik: str,
        accession: str,
        form: str | None,
        cfg: "Settings",
    ) -> "AcquireOutcome": ...


@dataclass
class AcquireOutcome:
    """What one acquisition attempt did."""

    acquired: bool
    fetched: bool = False
    reason: str | None = None
    detail: str | None = None


@dataclass
class FilingEvidenceResult:
    """The bridge's answer: real indexed content, or an honest reason."""

    state: str
    accession: str | None = None
    reason: str | None = None
    version_id: uuid.UUID | None = None
    chunk_count: int = 0
    #: True only when this call went to the network for a filing body.
    fetched: bool = False
    #: True when the filing was already searchable and nothing was fetched.
    reused: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.state == STATE_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "accession": self.accession,
            "reason": self.reason,
            "chunk_count": self.chunk_count,
            "fetched": self.fetched,
            "reused": self.reused,
            "notes": list(self.notes),
        }


async def ensure_filing_corpus_evidence(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    cik: str | int | None,
    accession: str | int | None,
    form: str | None,
    cfg: "Settings",
    acquire: FilingAcquirer | None = None,
    ready_only: bool = False,
) -> FilingEvidenceResult:
    """Guarantee that a filing is searchable, or say honestly why it is not.

    ``ready_only`` answers from the database and never acquires — the caller's
    acquisition budget is spent, and the honest answer for anything not already held is
    "not searchable, and this run will not make it so".

    Two outcomes and no third: either the retrieval backend can return content for this
    filing afterwards, or the result carries a structured reason. There is deliberately
    no "probably fine" — that is what a version row without chunks already was.

    Never raises. Evidence acquisition degrading must never end a research run.
    """
    canonical = canonical_accession(accession)
    if canonical is None:
        # Fails closed BEFORE anything else. An accession that is not eighteen digits
        # never reaches URL construction, so a traversal attempt cannot become a fetch.
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE, reason="malformed_accession"
        )

    if not getattr(cfg, "v3_filing_body_bridge_enabled", False):
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE, accession=canonical, reason="bridge_disabled"
        )
    if not getattr(cfg, "v3_corpus_enabled", False):
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE, accession=canonical, reason="corpus_disabled"
        )
    if company_id is None:
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE, accession=canonical, reason="no_company_identity"
        )

    # STATE A — already searchable. No network, no extraction, no duplicate chunks.
    # This is the branch that makes a repeat run free, and it is checked first for
    # exactly that reason.
    state = await filing_evidence_state(
        session, company_id=company_id, accession=canonical
    )
    if state.is_ready:
        return FilingEvidenceResult(
            state=STATE_READY,
            accession=canonical,
            version_id=state.version_id,
            chunk_count=state.indexable_chunk_count,
            reused=True,
            notes=["already searchable; no fetch and no extraction"],
        )

    if ready_only:
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE,
            accession=canonical,
            reason="acquire_budget_exhausted",
            notes=[
                "this filing is not searchable and was not acquired: the run's "
                "filing-body budget was already spent on other filings"
            ],
        )

    notes: list[str] = []
    if state.state == STATE_HISTORICAL_WITHOUT_CHUNKS:
        # STATE B — the case the cache made permanent. A cached document that cannot be
        # searched does not satisfy a corpus-evidence requirement, so reacquisition is
        # the correct answer rather than a cache hit.
        notes.append(
            "a document exists for this filing but carries no indexable chunks; "
            "reacquiring the official filing rather than reusing an unsearchable row"
        )

    normalized_cik = str(cik or "").strip()
    if not normalized_cik.isdigit():
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE,
            accession=canonical,
            reason="malformed_cik",
            notes=notes,
        )

    if acquire is None:
        from app.services.corpus.filing_acquisition import acquire_sec_filing

        acquire = acquire_sec_filing

    try:
        outcome = await acquire(
            session,
            company_id=company_id,
            cik=normalized_cik,
            accession=canonical,
            form=form,
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - acquisition must not end a run
        logger.exception("Filing acquisition failed for %s", canonical)
        return FilingEvidenceResult(
            state=STATE_UNAVAILABLE,
            accession=canonical,
            reason="acquisition_error",
            notes=[*notes, f"{type(exc).__name__}"],
        )

    if outcome.detail:
        notes.append(outcome.detail)

    # Re-read the state rather than believing the acquirer. Extraction can succeed while
    # indexing produces nothing — a partial outcome that would otherwise be recorded as
    # success and leave a specialist searching an empty document.
    after = await filing_evidence_state(
        session, company_id=company_id, accession=canonical
    )
    if after.is_ready:
        return FilingEvidenceResult(
            state=STATE_READY,
            accession=canonical,
            version_id=after.version_id,
            chunk_count=after.indexable_chunk_count,
            fetched=outcome.fetched,
            notes=notes,
        )

    return FilingEvidenceResult(
        state=STATE_UNAVAILABLE,
        accession=canonical,
        reason=outcome.reason or "no_indexable_content",
        fetched=outcome.fetched,
        notes=[
            *notes,
            after.detail
            or "acquisition completed without producing indexable chunks",
        ],
    )


__all__ = [
    "STATE_ABSENT",
    "STATE_HISTORICAL_WITHOUT_CHUNKS",
    "STATE_READY",
    "STATE_UNAVAILABLE",
    "AcquireOutcome",
    "FilingEvidenceResult",
    "FilingEvidenceState",
    "canonical_accession",
    "ensure_filing_corpus_evidence",
    "filing_evidence_state",
    "is_corpus_search_ready",
]
