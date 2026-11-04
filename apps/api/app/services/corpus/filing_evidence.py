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
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import func, select, update

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


def _document_fragment(accession_canonical: str, document_name: str | None) -> str | None:
    """``<accession>/<file>`` for ONE named document of a filing, or ``None`` when the
    name could never be a safe Archives file name (ADR-077: a results 6-K is read
    through its press release or its iXBRL statements, not its cover page)."""
    from app.services.sources.sec_filing_documents import build_document_url

    name = str(document_name or "").strip()
    if not name or build_document_url("1", accession_canonical, name) is None:
        return None
    return f"{_url_fragment(accession_canonical)}/{name}"


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
    document_name: str | None = None,
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
    if document_name is not None:
        # ONE named document of the filing (ADR-077). It may be an exhibit — the caller
        # asked for that exhibit — and the filing's cover page cannot answer for it.
        fragment = _document_fragment(canonical, document_name)
        if fragment is None:
            return FilingEvidenceState(
                state=STATE_ABSENT, accession=canonical,
                detail="document name could not be validated",
            )
        return await _evidence_state_for_fragment(
            session, company_id=company_id, fragment=fragment, label=canonical,
            accept_exhibit=True,
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
    accept_exhibit: bool = False,
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
            session, company_id=company_id, fragment=fragment,
            exact_document=accept_exhibit,
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

    if (
        derivation is not None
        and indexable > 0
        and not accept_exhibit
        and is_exhibit_url(version.canonical_url)
    ):
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
    document_name: str | None = None,
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
    if document_name is not None:
        fragment = _document_fragment(canonical, document_name)
        if fragment is None:
            return None
        return await current_version_for_fragment(
            session, company_id=company_id, fragment=fragment, exact_document=True
        )
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
    exact_document: bool = False,
) -> ResearchDocumentVersion | None:
    """The CURRENT corpus version whose canonical URL carries ``fragment``, for one
    company. The shared body of both lookups above.

    ``exact_document`` (ADR-077): ``fragment`` is ``<accession>/<file>`` and the URL
    must END with it — escaped, so the ``_`` in ``exh_991.htm`` is not a wildcard.

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
                        if whole_segment or exact_document
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
    if exact_document:
        rows = [v for v in rows if str(v.canonical_url or "").endswith("/" + fragment)]
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
        document_name: str | None = None,
        filing_date: date | None = None,
        report_period: Any = None,
    ) -> "AcquireOutcome": ...


def filing_date_of(raw: Any) -> date | None:
    """The SEC filing date as a ``date``, or ``None``. Accepts a ``date``, a
    ``datetime`` or an ISO ``YYYY-MM-DD`` string (EDGAR submissions ``filingDate``, a
    6-K index row); anything else is ``None`` — never a guess."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()[:10]
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


async def stamp_filing_date(
    session: Any, *, version_id: uuid.UUID | None, filing_date: Any
) -> int:
    """ADR-076 review: anchor — date a filing's corpus version and chunks. Never raises.

    Sets ``published_at`` to the SEC FILING date (never the period end, never the
    retrieval time) on the version and on every chunk of it, but only where it is NULL:
    a date already recorded is never overwritten, so this is idempotent and also
    backfills a filing first ingested undated (CLLS2-5) when it is met again. Returns how
    many chunks were dated.
    """
    when = filing_date_of(filing_date)
    if version_id is None or when is None:
        return 0
    try:
        async with session.begin_nested():
            await session.execute(
                update(ResearchDocumentVersion)
                .where(
                    ResearchDocumentVersion.id == version_id,
                    ResearchDocumentVersion.published_at.is_(None),
                )
                .values(published_at=when)
                .execution_options(synchronize_session="fetch")
            )
            result = await session.execute(
                update(ResearchDocumentChunk)
                .where(
                    ResearchDocumentChunk.research_document_version_id == version_id,
                    ResearchDocumentChunk.published_at.is_(None),
                )
                .values(published_at=when)
                .execution_options(synchronize_session="fetch")
            )
        return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001 - an undated filing is still a usable filing
        logger.warning("Could not record the filing date on version %s", version_id)
        return 0


async def reconcile_filing_period(
    session: Any,
    *,
    version_id: uuid.UUID | None,
    filing_date: Any = None,
    report_period: Any = None,
) -> bool:
    """ADR-080 — repair a filing's stored period on re-encounter. Never raises.

    The QA campaign's Cellectis corpus already holds 6-Ks stored as "interim report for
    2027-H2 / 2028-H2": their periods were read from a cash-runway sentence, and a rerun
    reuses those rows without re-reading them. So, on the same scope as
    ``stamp_filing_date`` (this version and its chunks only):

    * a stored period that had not BEGUN by the version's published date (or, when it is
      undated, the filing date) is a forecast and is cleared — on the version, on its
      chunks that carry it, and on the document when the document carries that same
      period;
    * when the regulator's period of report is known (``report_period``, a
      ``DocumentPeriod``) it fills the period left empty — never one that is known and
      had begun.

    The document's KEY is an identity and is not rewritten. Returns True when anything
    changed.
    """
    from app.services.corpus.documents import period_fields
    from app.services.sources.disclosure_period_policy import period_began_by

    if version_id is None:
        return False
    try:
        version = await session.get(ResearchDocumentVersion, version_id)
        if version is None:
            return False
        published = version.published_at or filing_date_of(filing_date)
        old = (version.period_key, version.period_type, version.period_basis)
        new = old
        if old[0] and not period_began_by(old[0], published, basis=old[2]):
            new = (None, None, None)
        known = getattr(report_period, "is_known", False)
        if new[0] is None and known:
            filled = period_fields(report_period)
            if filled[0] and period_began_by(filled[0], published, basis=filled[2]):
                new = filled
        if new == old:
            return False
        async with session.begin_nested():
            version.period_key, version.period_type, version.period_basis = new
            chunk_scope = (
                ResearchDocumentChunk.period_key == old[0]
                if old[0] is not None
                else ResearchDocumentChunk.period_key.is_(None)
            )
            await session.execute(
                update(ResearchDocumentChunk)
                .where(
                    ResearchDocumentChunk.research_document_version_id == version_id,
                    chunk_scope,
                )
                .values(period_key=new[0], period_type=new[1])
                .execution_options(synchronize_session="fetch")
            )
            document = await session.get(ResearchDocument, version.research_document_id)
            if document is not None and (
                document.period_key == old[0] or document.period_key is None
            ):
                document.period_key, document.period_type = new[0], new[1]
            await session.flush()
        logger.info(
            "Filing period reconciled on version %s: %s -> %s", version_id, old[0], new[0]
        )
        return True
    except Exception:  # noqa: BLE001 - a stale period must not end a run
        logger.warning("Could not reconcile the filing period on version %s", version_id)
        return False


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
    document_name: str | None = None,
    filing_date: Any = None,
    report_period: Any = None,
) -> FilingEvidenceResult:
    """Guarantee that a filing is searchable, or say honestly why it is not.

    ``report_period`` (ADR-080) is the period EDGAR's period of report says the filing
    covers (``document_period.sec_period_of_report``), when the caller could read one.
    It is handed to the acquirer, which stores it as the version's period ahead of
    anything read from the filing's words, and on every re-encounter a stored period
    that had not begun by the filing's date is cleared (``reconcile_filing_period``).

    ``filing_date`` (ADR-076 review: anchor) is the SEC filing date the caller already
    holds (EDGAR ``filingDate``, a 6-K index row). A searchable filing's version and
    chunks are dated with it where they carry no date — on acquisition, and on every
    re-encounter of a filing first stored undated. Never the period end.

    ``document_name`` (ADR-077) names ONE document of the filing — a results 6-K's
    press release or iXBRL statements — and readiness is then judged on that document,
    not on whichever document of the accession happens to be held.

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
        session, company_id=company_id, accession=canonical, document_name=document_name
    )
    if state.is_ready:
        await stamp_filing_date(
            session, version_id=state.version_id, filing_date=filing_date
        )
        await reconcile_filing_period(
            session, version_id=state.version_id, filing_date=filing_date,
            report_period=report_period,
        )
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

    # Each only when known, so an acquirer written before it keeps working: the 6-K
    # document (ADR-077), and the filing date, stamped before indexing so every backend
    # indexes it (ADR-076 review: anchor).
    optional: dict[str, Any] = {}
    if document_name is not None:
        optional["document_name"] = document_name
    if filing_date_of(filing_date) is not None:
        optional["filing_date"] = filing_date_of(filing_date)
    if getattr(report_period, "is_known", False):
        optional["report_period"] = report_period
    try:
        outcome = await acquire(
            session,
            company_id=company_id,
            cik=normalized_cik,
            accession=canonical,
            form=form,
            cfg=cfg,
            **optional,
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
        session, company_id=company_id, accession=canonical, document_name=document_name
    )
    if after.is_ready:
        await stamp_filing_date(
            session, version_id=after.version_id, filing_date=filing_date
        )
        await reconcile_filing_period(
            session, version_id=after.version_id, filing_date=filing_date,
            report_period=report_period,
        )
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
    "filing_date_of",
    "filing_evidence_state",
    "reconcile_filing_period",
    "stamp_filing_date",
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
    sixk_selector: Any = None,
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

    SEC registrants only — but an issuer is an SEC registrant when ANY verified line of it
    is (ADR-075): a Paris-listed company whose same-issuer ADS trades on Nasdaq has its
    20-F read through that line, and the basis is recorded under ``sec_basis``.

    A foreign private issuer reports its quarter or half year on a Form 6-K, not a 10-Q
    (ADR-077). When no 10-Q is listed and the annual report is not a 10-K, the quarterly
    slot takes the newest 6-K whose own documents show it is a RESULTS release
    (``sec_6k_results.select_results_6k``: bounded index reads, no new host) and reads
    the document that carries the results — the press release or the iXBRL statements —
    through the same bridge. No such 6-K among those examined stays ``not_listed``,
    with the reason.

    Never raises; returns what happened for each filing.
    """
    from app.services.entities.sec_listing import sec_listing_for

    out: dict[str, Any] = {"annual": None, "quarterly": None}
    if not (
        getattr(cfg, "v3_filings_tool_enabled", False)
        and getattr(cfg, "v3_filing_body_bridge_enabled", False)
    ):
        out["skipped"] = "filings tool or filing bridge disabled"
        return out
    basis = await sec_listing_for(session, company, cfg=cfg)
    if basis is None:
        out["skipped"] = "not an SEC registrant"
        return out
    ticker, exchange = basis.ticker, basis.exchange
    if basis.is_sibling:
        out["sec_basis"] = basis.to_dict()
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
            **({"cik": basis.cik} if basis.cik else {}),
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
        if event is None and slot == "quarterly" and _reads_6k_interims(out, events):
            out[slot] = await _ensure_results_6k(
                session, company=company, cfg=cfg, events=events,
                cik=getattr(listed, "cik", None), selector=sixk_selector,
                out_annual=out.get("annual"),
            )
            continue
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
            filing_date=getattr(event, "filing_date", None),
            report_period=_report_period_of(event, events),
        )
        out[slot] = {
            "form": str(getattr(event, "form_type", "")),
            "filing_date": str(getattr(event, "filing_date", "") or "") or None,
            **result.to_dict(),
        }
    return out


def _report_period_of(event: Any, events: list[Any], *, results_title: str | None = None) -> Any:
    """ADR-080 — the period EDGAR's period of report says ``event`` covers, read with
    its form; ``UNKNOWN`` when it cannot be read. A 10-Q's quarter needs the fiscal year
    end, taken from the newest annual filing's own period of report in the same list."""
    from app.services.sources.document_period import (
        fiscal_year_end_month_of,
        sec_period_of_report,
    )

    annual = next(
        (e for e in events if str(getattr(e, "form_type", "") or "").upper() in ANNUAL_FORMS),
        None,
    )
    return sec_period_of_report(
        form=str(getattr(event, "form_type", "") or ""),
        report_date=getattr(event, "report_date", None),
        filing_date=getattr(event, "filing_date", None),
        fiscal_year_end_month=(
            fiscal_year_end_month_of(getattr(annual, "report_date", None))
            if annual is not None else None
        ),
        results_title=results_title,
    )


def _reads_6k_interims(out: dict[str, Any], events: list[Any]) -> bool:
    """A 6-K filer with no 10-K: only a foreign private issuer files a 6-K, and one
    that files a 10-K reports its quarters on a 10-Q (ADR-077)."""
    annual = out.get("annual") if isinstance(out.get("annual"), dict) else {}
    if str((annual or {}).get("form") or "").upper() == "10-K":
        return False
    from app.services.sources.sec_6k_results import SIXK_FORMS

    return any(str(getattr(e, "form_type", "") or "").upper() in SIXK_FORMS for e in events)


async def _ensure_results_6k(
    session: Any,
    *,
    company: Any,
    cfg: "Settings",
    events: list[Any],
    cik: Any,
    selector: Any = None,
    out_annual: Any = None,
) -> dict[str, Any]:
    """The quarterly slot of a 20-F / 40-F filer: its newest results 6-K, read through
    the bridge, or ``not_listed`` with the reason. Never raises."""
    from app.services.sources.sec_6k_results import select_results_6k

    forms = ["6-K", "6-K/A"]
    if not getattr(cfg, "primary_document_sec_body_enabled", False):
        # No body would be fetched, so no index page is read to choose one either.
        return {"state": "not_listed", "forms": forms,
                "reason": "SEC filing-body fetch is disabled"}
    # Only a 6-K filed AFTER the annual report can carry a later period (ADR-077,
    # review of PR #313): a Q4 6-K filed before the 40-F reports the same year.
    annual = out_annual if isinstance(out_annual, dict) else {}
    since = annual.get("filing_date") if annual.get("form") else None
    # The annual report's fiscal period end (EDGAR's period of report for a 20-F /
    # 40-F): a 6-K whose own period is not after it is the same year again.
    annual_event = next(
        (
            e for e in events
            if str(getattr(e, "form_type", "") or "").upper() in ANNUAL_FORMS
            and str(getattr(e, "accession_number", "") or "")
            and str(getattr(e, "filing_date", "") or "") == str(since or "")
        ),
        None,
    ) if since else None
    annual_period_end = (
        str(getattr(annual_event, "report_date", "") or "") or None if annual_event else None
    )
    try:
        selection = await (selector or select_results_6k)(
            events, cik=cik, cfg=cfg, since=since, annual_period_end=annual_period_end
        )
    except Exception as exc:  # noqa: BLE001 - selection failing costs the slot only
        return {"state": "not_listed", "forms": forms,
                "reason": f"6-K selection failed ({type(exc).__name__})"}
    if selection.event is None:
        return {"state": "not_listed", "forms": forms, "reason": selection.reason,
                "selection": selection.selection_dict()}
    event = selection.event
    result = await ensure_filing_corpus_evidence(
        session,
        company_id=getattr(company, "id", None),
        cik=cik,
        accession=getattr(event, "accession_number", None),
        form=str(getattr(event, "form_type", "")),
        cfg=cfg,
        document_name=selection.document_name,
        filing_date=getattr(event, "filing_date", None),
        # ADR-080: a results 6-K's period of report is its period only when the
        # document's own headline names the period that ends on that date.
        report_period=_report_period_of(event, events, results_title=selection.title),
    )
    return {
        "form": str(getattr(event, "form_type", "")),
        "filing_date": str(getattr(event, "filing_date", "") or "") or None,
        "document": selection.document_name,
        "basis": selection.basis,
        # The document's own headline when it was read; the issuer's declared period
        # end only when the evidence was its iXBRL statements. Neither is ever guessed.
        "title": selection.title,
        "period_end": selection.period_end,
        "selection": selection.selection_dict(),
        **result.to_dict(),
    }
