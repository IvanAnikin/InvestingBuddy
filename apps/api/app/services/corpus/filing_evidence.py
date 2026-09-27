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
import re
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
    #: A non-SEC transport's own document id (never an accession, never a URL).
    document_ref: str | None = None
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
    return await _evidence_state_for_fragment(
        session, company_id=company_id, fragment=_url_fragment(canonical), label=canonical
    )


def url_has_document_segment(url: str | None, ref: str) -> bool:
    """True when ``ref`` is a WHOLE path segment of ``url`` (optionally with an
    extension) — ``…/NSM/PRN/<ref>.html``, ``…/NI-000131364/NI-000131364.pdf`` — or
    the EXACT value of one of its query parameters — the ASX's own announcement address
    ``…/displayAnnouncement.do?display=pdf&idsId=<ref>``.

    Never a substring: ``NI-000131364`` must not match ``NI-0001313641``, and a ref that
    happens to be a word of the host or path must not match every document.
    """
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(str(url or ""))
    for segment in parts.path.split("/"):
        if segment == ref or segment.startswith(ref + "."):
            return True
    # Only a parameter that NAMES a document — never an arbitrary value such as a page
    # number, which an all-digit id could otherwise equal.
    return any(
        key in _DOCUMENT_ID_PARAMS and value == ref for key, value in parse_qsl(parts.query)
    )


#: Query parameters a venue uses for its own document id (the ASX announcement address).
_DOCUMENT_ID_PARAMS: frozenset[str] = frozenset({"idsId"})


#: An official document identifier as it appears inside the transport's canonical URL:
#: an NSM disclosure id ("NI-000131364", a UUID), an ASX document key
#: ("2924-03139714-6A1345626"). Letters, digits, dot, dash and underscore only — so
#: it can never be a path, a wildcard or a query.
_DOCUMENT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,79}$")


def canonical_document_ref(raw: str | None) -> str | None:
    """A transport's own document identifier, validated, or ``None``."""
    value = str(raw or "").strip()
    return value if _DOCUMENT_REF_RE.match(value) else None


async def document_evidence_state(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    document_ref: str | None,
) -> FilingEvidenceState:
    """The same A / B / C classification as :func:`filing_evidence_state`, for a
    non-SEC official document identified by its transport's own document id.

    One predicate, not two: both entry points resolve to the same company-scoped,
    current-version-required, indexed-chunks-required check below.
    """
    ref = canonical_document_ref(document_ref)
    if ref is None:
        return FilingEvidenceState(
            state=STATE_ABSENT, detail="document reference could not be validated"
        )
    return await _evidence_state_for_fragment(
        session, company_id=company_id, fragment=ref, label=ref, document_ref=ref
    )


async def _evidence_state_for_fragment(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    fragment: str,
    label: str,
    document_ref: str | None = None,
) -> FilingEvidenceState:
    # An accession for the SEC entry point; the transport's document id otherwise. The
    # two are never mixed: a document ref must not reach anything that builds an SEC
    # URL from ``accession``.
    canonical = None if document_ref else label
    ref_kw: dict[str, Any] = {"document_ref": document_ref}
    if company_id is None:
        return FilingEvidenceState(
            state=STATE_ABSENT, accession=canonical, detail="no company identity",
            **ref_kw,
        )

    if document_ref:
        version = await current_version_for_document_ref(
            session, company_id=company_id, document_ref=document_ref
        )
    else:
        version = await current_version_for_fragment(
            session, company_id=company_id, fragment=fragment
        )

    if version is None:
        # A SUPERSEDED version is not a fallback.
        #
        # The query above REQUIRES `is_current`, and that is load-bearing rather than
        # tidy. Expressing "current" as an `ORDER BY is_current DESC` ranks a current
        # version first but does not require one to exist — so where every matching
        # version had been superseded, a stale one was selected and could satisfy READY
        # on the strength of chunks belonging to a reading of the document the platform
        # has already replaced. The bridge would then report `reused`, skip
        # reacquisition, and leave a specialist searching the old reading for ever.
        superseded = await _superseded_version_exists(
            session, company_id=company_id, fragment=fragment,
            whole_segment=bool(document_ref),
        )
        extracted_id = await _historical_extracted_document_id(
            session, company_id=company_id, fragment=fragment,
            whole_segment=bool(document_ref),
        )
        if superseded:
            return FilingEvidenceState(
                state=STATE_HISTORICAL_WITHOUT_CHUNKS,
                accession=canonical, **ref_kw,
                extracted_document_id=extracted_id,
                detail=(
                    "every corpus version of this filing is SUPERSEDED; a stale "
                    "reading is not evidence, so the filing must be reacquired"
                ),
            )
        if extracted_id is not None:
            return FilingEvidenceState(
                state=STATE_HISTORICAL_WITHOUT_CHUNKS,
                accession=canonical, **ref_kw,
                extracted_document_id=extracted_id,
                detail=(
                    "a V2 extracted document exists with bounded excerpts and no corpus "
                    "version; excerpts are not searchable filing content"
                ),
            )
        return FilingEvidenceState(state=STATE_ABSENT, accession=canonical, **ref_kw)

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

    if derivation is not None and indexable > 0 and is_exhibit_url(version.canonical_url):
        # Searchable, and not the filing. Reacquiring now selects the body, because the
        # selector no longer mistakes an "…exhibit96…" name for a body document.
        return FilingEvidenceState(
            state=STATE_HISTORICAL_WITHOUT_CHUNKS,
            accession=canonical, **ref_kw,
            version_id=version.id,
            derivation_id=derivation.id,
            extracted_document_id=version.extracted_document_id,
            chunk_count=chunk_count,
            indexable_chunk_count=indexable,
            detail=(
                "the only searchable document for this filing is an EXHIBIT; the "
                "filing body was never acquired"
            ),
        )

    if derivation is not None and indexable > 0:
        return FilingEvidenceState(
            state=STATE_READY,
            accession=canonical, **ref_kw,
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
        accession=canonical, **ref_kw,
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


#: How many current versions of one accession are considered. An accession is a folder,
#: and a 10-K's folder holds the body and its exhibits.
_MAX_VERSIONS_PER_FILING = 8
#: Candidates read for a document ref before the exact segment / id check. The SQL
#: prefilter is a substring, so near-miss addresses must not crowd out the true one.
_MAX_REF_CANDIDATES = 64


def is_exhibit_url(url: str | None) -> bool:
    """Is this URL an EXHIBIT of a filing rather than the filing body?

    Decided on the file name the issuer published, by the same rule the document
    selector uses, so "which file is the filing" has one answer in this codebase.
    """
    from app.services.sources.sec_filing_documents import is_exhibit_name

    name = (str(url or "").rstrip("/").rsplit("/", 1)[-1]).strip()
    return is_exhibit_name(name)


async def current_version_for_filing(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    accession: str | int | None,
) -> ResearchDocumentVersion | None:
    """The CURRENT corpus version of one filing, for one company, or ``None``.

    One definition, used by the readiness predicate and by the acquirer that has to know
    which version it just produced. Two lookups would be two chances to disagree about
    which version a filing means.

    Company scope is a join because ``company_id`` lives on the LOGICAL document, not on
    the retrieval — getting that wrong is exactly how one issuer's filing would come to
    satisfy another's request.
    """
    canonical = canonical_accession(accession)
    if canonical is None or company_id is None:
        return None
    return await current_version_for_fragment(
        session, company_id=company_id, fragment=_url_fragment(canonical)
    )


async def current_version_for_document_ref(
    session: Any, *, company_id: uuid.UUID | None, document_ref: str | None
) -> ResearchDocumentVersion | None:
    """:func:`current_version_for_filing` for a non-SEC official document id."""
    ref = canonical_document_ref(document_ref)
    if ref is None or company_id is None:
        return None
    version = await current_version_for_fragment(
        session, company_id=company_id, fragment=ref, whole_segment=True
    )
    if version is not None:
        return version
    return await _current_version_via_attempt(session, company_id=company_id, ref=ref)


async def _current_version_via_attempt(
    session: Any, *, company_id: uuid.UUID, ref: str
) -> ResearchDocumentVersion | None:
    """The current version of a document this company ACQUIRED from ``ref``, when the
    same bytes had first been stored under another address.

    Documents are deduplicated by content hash and keep their FIRST address, so a
    regulator-stored annual report already fetched from the issuer's own site keeps the
    issuer's URL — and a lookup by the regulator's document id would never find it, and
    the document would be re-acquired on every run. The ingestion attempt records what
    was fetched from WHERE for WHICH company; its content hash leads to the version.
    Company-scoped at both ends.
    """
    from app.models.document_ingestion_attempt import DocumentIngestionAttempt

    attempts = (
        await session.execute(
            select(
                DocumentIngestionAttempt.canonical_url,
                DocumentIngestionAttempt.content_hash,
            )
            .where(
                DocumentIngestionAttempt.company_id == company_id,
                DocumentIngestionAttempt.content_hash.is_not(None),
                DocumentIngestionAttempt.canonical_url.contains(ref, autoescape=True),
            )
            # Newest first, so a document re-fetched many times with changed bytes
            # is judged by its latest retrieval, not by an arbitrary eight.
            .order_by(DocumentIngestionAttempt.attempted_at.desc())
            .limit(_MAX_REF_CANDIDATES)
        )
    ).all()
    hashes = sorted({
        str(content_hash).lower()
        for url, content_hash in attempts
        if content_hash and url_has_document_segment(url, ref)
    })
    if not hashes:
        return None
    return (
        await session.execute(
            select(ResearchDocumentVersion)
            .join(
                ResearchDocument,
                ResearchDocument.id == ResearchDocumentVersion.research_document_id,
            )
            .where(
                ResearchDocument.company_id == company_id,
                ResearchDocumentVersion.content_hash.in_(hashes),
                ResearchDocumentVersion.is_current.is_(True),
            )
            .order_by(ResearchDocumentVersion.canonical_url)
            .limit(1)
        )
    ).scalar_one_or_none()


async def current_version_for_fragment(
    session: Any,
    *,
    company_id: uuid.UUID | None,
    fragment: str,
    whole_segment: bool = False,
) -> ResearchDocumentVersion | None:
    """The CURRENT corpus version whose canonical URL carries ``fragment``, for one
    company. The shared body of both lookups above.

    ``whole_segment`` (document refs): the SQL narrows by an ESCAPED ``ref`` substring
    and the result is then required to carry ``ref`` as a whole path segment or as a
    document-id query value — so a wildcard character can never widen the match and one
    id never answers for another.
    The SEC path keeps its fixed-width accession fragment, unchanged.
    """
    if not fragment or company_id is None:
        return None
    rows = (
        (
            await session.execute(
                select(ResearchDocumentVersion)
                .join(
                    ResearchDocument,
                    ResearchDocument.id == ResearchDocumentVersion.research_document_id,
                )
                .where(
                    ResearchDocument.company_id == company_id,
                    (
                        ResearchDocumentVersion.canonical_url.contains(
                            fragment, autoescape=True
                        )
                        if whole_segment
                        else ResearchDocumentVersion.canonical_url.contains(fragment)
                    ),
                    # REQUIRED, not merely preferred. See `filing_evidence_state`.
                    ResearchDocumentVersion.is_current.is_(True),
                )
                # Deterministic: one accession can hold several documents, and an
                # arbitrary `limit(1)` made "which version is this filing" depend on the
                # planner. Ordering by URL also makes the preference below stable.
                .order_by(ResearchDocumentVersion.canonical_url)
                .limit(_MAX_REF_CANDIDATES if whole_segment else _MAX_VERSIONS_PER_FILING)
            )
        )
        .scalars()
        .all()
    )
    if whole_segment:
        rows = [v for v in rows if url_has_document_segment(v.canonical_url, fragment)]
    if not rows:
        return None
    # THE FILING BODY, not an exhibit filed beside it. A 10-K's exhibits live in the same
    # accession folder and match the same fragment, so an exhibit could answer "is this
    # filing searchable?" — which is how MP Materials' corpus came to hold a 500-page
    # technical report summary and not the 10-K.
    for version in rows:
        if not is_exhibit_url(version.canonical_url):
            return version
    return rows[0]


async def _superseded_version_exists(
    session: Any, *, company_id: uuid.UUID, fragment: str, whole_segment: bool = False
) -> bool:
    """True when this company holds only non-current versions of this filing."""
    urls = (
        await session.execute(
            select(ResearchDocumentVersion.canonical_url)
            .join(
                ResearchDocument,
                ResearchDocument.id == ResearchDocumentVersion.research_document_id,
            )
            .where(
                ResearchDocument.company_id == company_id,
                (
                    ResearchDocumentVersion.canonical_url.contains(
                        fragment, autoescape=True
                    )
                    if whole_segment
                    else ResearchDocumentVersion.canonical_url.contains(fragment)
                ),
            )
            .limit(_MAX_REF_CANDIDATES if whole_segment else _MAX_VERSIONS_PER_FILING)
        )
    ).scalars().all()
    if whole_segment:
        urls = [u for u in urls if url_has_document_segment(u, fragment)]
    return bool(urls)


async def _historical_extracted_document_id(
    session: Any, *, company_id: uuid.UUID, fragment: str, whole_segment: bool = False
) -> uuid.UUID | None:
    """The V2 ``ExtractedDocument`` for this filing, if this company has one."""
    from app.models.extracted_document import ExtractedDocument

    rows = (
        await session.execute(
            select(ExtractedDocument.id, ExtractedDocument.canonical_url)
            .where(
                ExtractedDocument.company_id == company_id,
                (
                    ExtractedDocument.canonical_url.contains(fragment, autoescape=True)
                    if whole_segment
                    else ExtractedDocument.canonical_url.contains(fragment)
                ),
            )
            .limit(_MAX_REF_CANDIDATES if whole_segment else _MAX_VERSIONS_PER_FILING)
        )
    ).all()
    for doc_id, url in rows:
        if not whole_segment or url_has_document_segment(url, fragment):
            return doc_id
    return None


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
    #: True when an acquisition was ATTEMPTED — which is the bounded resource, not
    #: ``fetched``. An attempt that fails before the body (an unresolvable accession,
    #: say) has still made SEC index requests, so a caller budgeting on ``fetched``
    #: would let a list of unresolvable filings make unbounded network calls.
    attempted: bool = False
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
            "attempted": self.attempted,
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
            # The attempt happened, so it consumes a slot. A failure that refunded its
            # budget would let a list of failing filings retry without limit.
            attempted=True,
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
            attempted=True,
            notes=notes,
        )

    return FilingEvidenceResult(
        state=STATE_UNAVAILABLE,
        accession=canonical,
        reason=outcome.reason or "no_indexable_content",
        fetched=outcome.fetched,
        attempted=True,
        notes=[
            *notes,
            after.detail
            or "acquisition completed without producing indexable chunks",
        ],
    )


__all__ = [
    "canonical_document_ref",
    "url_has_document_segment",
    "current_version_for_document_ref",
    "current_version_for_fragment",
    "document_evidence_state",
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


#: The documents research on a company cannot do without, in order of preference.
ANNUAL_FORMS: tuple[str, ...] = ("10-K", "20-F", "40-F")
QUARTERLY_FORMS: tuple[str, ...] = ("10-Q",)
#: How far back the regulator's list is read for them: an annual report is at most
#: ~13 months old.
CORE_FILINGS_LOOKBACK_DAYS = 420


async def ensure_core_filings(
    session: Any,
    *,
    company: Any,
    cfg: "Settings",
    provider: Any = None,
) -> dict[str, Any]:
    """Put the subject's LATEST annual report and quarterly report into the corpus.

    V3.18 live acceptance, SCCO: the corpus held the 2026-Q2 10-Q and the 10-K's exhibit
    index, not the 10-K itself — so the business, segment, reserves and production
    questions reported that the evidence "contains no product, segment or revenue data".
    The bridge ran only inside ``get_recent_filings``, on whatever that call listed
    (8-Ks first) under a three-attempt budget. The research now secures the two filings
    every question depends on BEFORE it asks them — through the same bridge, with the
    same guarantees: accessions from the regulator, URLs built from code constants, never
    from a model.

    SEC registrants only. Never raises; returns what happened for each filing.
    """
    from app.services.exchange_registry import is_sec_eligible

    out: dict[str, Any] = {"annual": None, "quarterly": None}
    if not (
        getattr(cfg, "v3_filings_tool_enabled", False)
        and getattr(cfg, "v3_filing_body_bridge_enabled", False)
    ):
        out["skipped"] = "filings tool or filing bridge disabled"
        return out
    ticker = getattr(company, "ticker", None)
    exchange = getattr(company, "exchange", None)
    if not ticker or not is_sec_eligible(exchange):
        out["skipped"] = "not an SEC registrant"
        return out
    if provider is None:
        from app.integrations.providers.sec_recent_filings_provider import (
            SecRecentFilingsProvider,
        )

        provider = SecRecentFilingsProvider()
    try:
        listed = await provider.get_recent_events(
            ticker,
            exchange=exchange,
            lookback_days=CORE_FILINGS_LOOKBACK_DAYS,
            max_events=80,
        )
    except Exception as exc:  # noqa: BLE001 - discovery failing costs the step only
        out["skipped"] = f"filing list unavailable ({type(exc).__name__})"
        return out
    events = sorted(
        getattr(listed, "events", []) or [],
        key=lambda e: str(getattr(e, "filing_date", "") or ""),
        reverse=True,
    )
    for slot, forms in (("annual", ANNUAL_FORMS), ("quarterly", QUARTERLY_FORMS)):
        event = next(
            (e for e in events if str(getattr(e, "form_type", "")).upper() in forms), None
        )
        if event is None:
            out[slot] = {"state": "not_listed", "forms": list(forms)}
            continue
        result = await ensure_filing_corpus_evidence(
            session,
            company_id=getattr(company, "id", None),
            cik=getattr(listed, "cik", None),
            accession=getattr(event, "accession_number", None),
            form=str(getattr(event, "form_type", "")),
            cfg=cfg,
        )
        out[slot] = {
            "form": str(getattr(event, "form_type", "")),
            "filing_date": str(getattr(event, "filing_date", "") or "") or None,
            **result.to_dict(),
        }
    return out
