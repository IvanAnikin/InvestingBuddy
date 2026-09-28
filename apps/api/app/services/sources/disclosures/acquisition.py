"""The acquisition bridge: an official disclosure → searchable, citable corpus content.

The V3.16 filing bridge's contract, for a non-SEC transport:

* **READY first, and free.** A document already searchable for this company answers from
  the database — no request, no extraction, no duplicate chunks. A repeat run is free.
* **Content, never metadata.** Only the fetched document's own text enters the corpus.
  A headline, a type or a date is never stored as content and never cited.
* **Identity at the content, too.** A fetched document must NAME the issuer (a
  distinctive word of the exchange's own name for it) or it is refused — measured: an
  id from the wrong id space returned another company's PDF.
* **URLs from identifiers.** UK: the NSM artefact path validated against the record's
  own id. ASX: the announcement's display page, whose attached-document address must be
  exactly an ``announcements.asx.com.au/asxpdf/…pdf``. Fetched through
  ``live_primary_document_extractor`` (allowlist pinned to that one host, DNS pinning,
  redirect re-checks, byte cap, timeout, PDF magic bytes).
* **One corpus.** ``persist_primary_document_artifacts`` (raw artifact, V2 row, corpus
  version, derivation, chunks), the ingestion attempt (so the same bytes reached from
  another address are still recognised), ``index_version`` — then readiness is RE-READ,
  never assumed.
* **Never raises.** A failed acquisition is a reason from the closed vocabulary.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.sources.disclosures.model import (
    RANK_MATERIAL,
    REASON_BUDGET_EXHAUSTED,
    REASON_CONNECTOR_DISABLED,
    REASON_DATA_NOT_SOURCED,
    REASON_EXTRACTION_FAILED,
    REASON_IDENTITY_UNVERIFIED,
    REASON_NOT_INDEXED,
    REASON_PRIMARY_DOCUMENT_UNAVAILABLE,
    REASON_VENUE_NOT_COVERED,
    SOURCE_ASX_ANNOUNCEMENTS,
    SOURCE_UK_FCA_NSM,
    VENUE_ASX,
    VENUE_LSE,
    DisclosureEvidenceResult,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_RESULTS_RELEASE,
)

logger = logging.getLogger(__name__)

#: The closed source-type vocabulary persisted on ``ExtractedDocument`` — it is what
#: carries the title-only period policy through the database.
_SOURCE_TYPES = {
    SOURCE_UK_FCA_NSM: "uk_nsm_disclosure",
    SOURCE_ASX_ANNOUNCEMENTS: "asx_announcement",
}

#: How much of a fetched document is searched for the issuer's name.
_IDENTITY_TEXT_CHARS = 40_000


# ── Which source covers which company ──────────────────────────────────────── #

#: Venue codes a company row may carry for the same two markets.
_VENUE_ALIASES = {
    "LSE": VENUE_LSE, "LON": VENUE_LSE, "L": VENUE_LSE, "LN": VENUE_LSE, "AIM": VENUE_LSE,
    "XLON": VENUE_LSE, "AU": VENUE_ASX, "ASX": VENUE_ASX, "AX": VENUE_ASX, "XASX": VENUE_ASX,
}


def source_for(company: Any, cfg: Any) -> tuple[str | None, str | None]:
    """``(source_id, why-not)`` for a company's listing venue."""
    from app.services.exchange_registry import normalize_exchange

    venue = normalize_exchange(getattr(company, "exchange", None) or "")
    venue = _VENUE_ALIASES.get(venue.upper(), venue)
    if venue == VENUE_LSE:
        if not getattr(cfg, "v3_uk_nsm_disclosures_enabled", False):
            return None, REASON_CONNECTOR_DISABLED
        return SOURCE_UK_FCA_NSM, None
    if venue == VENUE_ASX:
        if not getattr(cfg, "v3_asx_announcements_enabled", False):
            return None, REASON_CONNECTOR_DISABLED
        return SOURCE_ASX_ANNOUNCEMENTS, None
    return None, REASON_VENUE_NOT_COVERED


async def list_disclosures(
    session: Any, company: Any, *, cfg: Any, fetcher: Any = None, poster: Any = None,
    now: datetime | None = None,
) -> DisclosureListing:
    """The issuer's recent official disclosures from the source covering its venue."""
    source_id, why = source_for(company, cfg)
    if source_id is None:
        return DisclosureListing(issuer=None, reason=why)
    if source_id == SOURCE_UK_FCA_NSM:
        from app.services.sources.disclosures.uk_nsm import list_uk_disclosures

        return await list_uk_disclosures(session, company, cfg=cfg, fetcher=fetcher,
                                         poster=poster, now=now)
    from app.services.sources.disclosures.asx import list_asx_announcements

    return await list_asx_announcements(session, company, cfg=cfg, fetcher=fetcher, now=now)


# ── Identity at the content ────────────────────────────────────────────────── #


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in raw if not unicodedata.combining(ch))


def issuer_name_tokens(name: str) -> list[str]:
    """Distinctive words of the exchange's own name for the issuer — the same rule the
    V3.19 issuer-domain check uses: legal suffixes and generic words never count."""
    from app.services.discovery.identity import _GENERIC_TOKENS, name_tokens

    return [t for t in name_tokens(name) if len(t) >= 4 and t not in _GENERIC_TOKENS]


def content_text(artifact: Any, limit: int = _IDENTITY_TEXT_CHARS) -> str:
    """The leading CONTENT of a fetched document — its blocks, else its excerpts — and
    never its title, which is the source's metadata."""
    extraction = getattr(artifact, "extraction", None)
    parts: list[str] = []
    total = 0
    for item in list(getattr(extraction, "blocks", None) or []) or list(
        getattr(extraction, "excerpts", None) or []
    ):
        text = str(getattr(item, "text", "") or "")
        parts.append(text)
        total += len(text)
        if total >= limit:
            break
    return " ".join(parts)[:limit]


def document_names_issuer(artifact: Any, issuer: VerifiedIssuer) -> bool:
    """True when the fetched document names THIS issuer.

    A distinctive word of the exchange's name for the issuer ("pensana", "ecograf",
    "medicus") as a whole word. A name with no distinctive word falls back to the full
    name, or the ticker in an exchange citation ("ASX: XYZ", "(LSE: XYZ)"). The
    document's metadata title is deliberately excluded from the search: the headline
    is metadata, and it is the CONTENT that must name the issuer.
    """
    from app.services.discovery.identity import normalised_name

    folded = _fold(content_text(artifact))
    # The issuer's WHOLE name (legal suffix dropped) as a phrase of whole words —
    # "rainbow rare earths", "australian strategic materials", "igo". One distinctive
    # word was too weak: "Australian" accepted a Lynas document, and a bare substring
    # found "IGO" inside "Indigo".
    words = re.sub(r"[^a-z0-9&]+", " ", folded)
    full = normalised_name(issuer.name)
    if full and re.search(r"(?<![a-z0-9])" + re.escape(full) + r"(?![a-z0-9])", words):
        return True
    ticker = re.escape(issuer.ticker.lower())
    return bool(re.search(r"\b(?:asx|lse|aim)\s*:\s*" + ticker + r"\b", folded))


# ── Readiness, acquisition, indexing ───────────────────────────────────────── #


async def _state(session: Any, company_id: uuid.UUID, ref: str) -> Any:
    from app.services.corpus.filing_evidence import document_evidence_state

    return await document_evidence_state(session, company_id=company_id, document_ref=ref)


async def _content_url(
    document: OfficialDocument, *, cfg: Any, fetcher: Any
) -> tuple[str | None, str | None, str | None]:
    """``(url, host, why-not)`` of the document's CONTENT."""
    if document.source_id == SOURCE_UK_FCA_NSM:
        from app.services.sources.disclosures.uk_nsm import NSM_ARTEFACT_HOST

        return document.content_url, NSM_ARTEFACT_HOST, None
    from app.services.sources.disclosures.asx import (
        ASX_HOST,
        ASX_PDF_HOST,
        pdf_url_from_display_page,
    )
    from app.services.sources.document_fetcher import safe_fetch_document

    page = await (fetcher or safe_fetch_document)(
        document.official_url, allowed_domains=(ASX_HOST,), cfg=cfg, resolve_ip=True,
    )
    content = getattr(page, "content", None) if getattr(page, "ok", False) else None
    if not content:
        return None, None, "the announcement's page could not be fetched"
    url = pdf_url_from_display_page(content.decode("utf-8", "replace"))
    if url is None:
        return None, None, "the announcement's page names no attached ASX document"
    return url, ASX_PDF_HOST, None


def _title(document: OfficialDocument) -> str:
    """The stored title: the venue's type and the issuer's headline, neutralised —
    the headline is external wording, and titles reach citations."""
    from app.schemas.catalyst import neutralize_forbidden_terms

    return neutralize_forbidden_terms(_raw_title(document)) or _raw_title(document)


def _raw_title(document: OfficialDocument) -> str:
    head = document.headline or ""
    category = document.venue_category or ""
    uk = document.source_id == SOURCE_UK_FCA_NSM
    if uk and category and category.lower() not in head.lower():
        return f"{category}: {head}"[:300]
    return head[:300]


async def _record_attempt(session: Any, artifact: Any, *, company_id: uuid.UUID,
                          source_type: str, cfg: Any) -> None:
    from app.services.document_ingestion_attempt_service import record_ingestion_attempts
    from app.services.sources.ingestion_attempts import artifact_to_attempt
    from app.services.sources.taxonomy import T1_PRIMARY_FILING

    try:
        await record_ingestion_attempts(
            session, company_id=company_id, agent_run_id=None,
            attempts=[artifact_to_attempt(artifact, source_type=source_type,
                                          source_tier=T1_PRIMARY_FILING)],
            cfg=cfg,
        )
    except Exception:  # noqa: BLE001 - an audit row failing never fails acquisition
        logger.warning("ingestion attempt could not be recorded")


async def _record_attempt_safely(session: Any, artifact: Any, **kw: Any) -> None:
    try:
        async with session.begin_nested():
            await _record_attempt(session, artifact, **kw)
    except Exception:  # noqa: BLE001 - an audit row never fails acquisition
        logger.warning("ingestion attempt could not be recorded")


async def _ensure_company_version(
    session: Any, *, artifact: Any, company_id: uuid.UUID, ref: str, cfg: Any
) -> None:
    """Give THIS company a corpus version of the bytes it just read, when the shared
    ``ExtractedDocument`` belongs to another company (or to none)."""
    from app.services.corpus.filing_evidence import current_version_for_document_ref

    if await current_version_for_document_ref(session, company_id=company_id,
                                              document_ref=ref) is not None:
        return
    try:
        from types import SimpleNamespace

        from sqlalchemy import select

        from app.models.extracted_document import ExtractedDocument
        from app.services.corpus.documents import ingest_extracted_document

        content_hash = getattr(getattr(artifact, "extraction", None), "content_hash", None)
        if not content_hash:
            return
        shared = (await session.execute(
            select(ExtractedDocument).where(ExtractedDocument.content_hash == content_hash)
            .limit(1))).scalar_one_or_none()
        if shared is None or shared.company_id == company_id:
            return
        # The same row, seen as this company's: the corpus version points back at the
        # shared V2 document, and it carries THIS company, THIS address and THIS
        # transport — the retrieval that actually happened.
        view = SimpleNamespace(
            id=shared.id, content_hash=shared.content_hash,
            canonical_url=getattr(artifact, "source_url", None) or shared.canonical_url,
            provider=getattr(artifact, "transport", None) or shared.provider,
            source_tier=shared.source_tier, company_id=company_id,
            source_type=getattr(artifact, "document_type", None) or shared.source_type,
            title=getattr(artifact, "title", None) or shared.title,
            mime_type=shared.mime_type, doc_date=getattr(artifact, "published_at", None),
            retrieved_at=getattr(artifact, "retrieved_at", None) or shared.retrieved_at,
            status=shared.status,
        )
        async with session.begin_nested():
            await ingest_extracted_document(
                session, artifact=artifact, document=view, cfg=cfg  # type: ignore[arg-type]
            )
    except Exception:  # noqa: BLE001 - costs this document's readiness only
        logger.exception("a company-scoped corpus version could not be created")


async def _index(session: Any, *, company_id: uuid.UUID, ref: str, cfg: Any) -> int:
    from app.services.corpus.filing_evidence import current_version_for_document_ref
    from app.services.corpus.indexing import index_version

    version = await current_version_for_document_ref(
        session, company_id=company_id, document_ref=ref)
    if version is None:
        return 0
    try:
        from app.services.corpus.search.factory import get_search_backend

        backend = get_search_backend(cfg, session=session)
    except Exception:  # noqa: BLE001
        return 0
    if backend is None:
        return 0
    try:
        result = await index_version(session, research_document_version_id=version.id,
                                     backend=backend, cfg=cfg)
    except Exception:  # noqa: BLE001
        logger.exception("indexing failed for a disclosure version")
        return 0
    return int(result.indexed) + int(result.updated)


async def _changed_since_acquired(
    session: Any, *, company_id: uuid.UUID, document: OfficialDocument
) -> bool:
    """True when the source changed this disclosure after the platform last acquired it.

    An NSM correction is re-filed at the SAME address (a new amendment of the same
    document), so the holding looks READY while describing the superseded filing. The
    source's own ``last_updated_date`` against this company's latest acquisition of the
    address decides; with no update time the holding stands.
    """
    if document.updated_at is None:
        return False
    try:
        from sqlalchemy import func, select

        from app.models.document_ingestion_attempt import DocumentIngestionAttempt
        from app.services.sources.redaction import canonicalize_source_url

        url = canonicalize_source_url(document.official_url) or document.official_url
        latest = (
            await session.execute(
                select(func.max(DocumentIngestionAttempt.attempted_at)).where(
                    DocumentIngestionAttempt.company_id == company_id,
                    DocumentIngestionAttempt.canonical_url.in_(
                        [url, document.official_url]),
                )
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - doubt keeps the holding
        return False
    if latest is None:
        return False
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest < document.updated_at


async def ensure_disclosure_evidence(
    session: Any,
    *,
    issuer: VerifiedIssuer,
    document: OfficialDocument,
    cfg: Any,
    fetcher: Any = None,
    extractor: Any = None,
    ready_only: bool = False,
) -> DisclosureEvidenceResult:
    """Make one official disclosure searchable for its issuer, or say why not."""
    from app.services.sources.disclosure_period_policy import PERIOD_POLICY_TITLE_ONLY

    ref = document.document_ref
    if not (getattr(cfg, "v3_corpus_enabled", False)
            and getattr(cfg, "primary_document_ingestion_enabled", False)
            and getattr(cfg, "report_citation_persistence_enabled", False)):
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_CONNECTOR_DISABLED,
                                        notes=["the corpus or primary-document persistence is off"])
    try:
        before = await _state(session, issuer.company_id, ref)
    except Exception as exc:  # noqa: BLE001
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_NOT_INDEXED, notes=[type(exc).__name__])
    stale = before.is_ready and await _changed_since_acquired(
        session, company_id=issuer.company_id, document=document)
    if before.is_ready and not stale:
        return DisclosureEvidenceResult(
            state="ready", document_ref=ref, chunk_count=before.indexable_chunk_count,
            reused=True, notes=["already searchable; no fetch and no extraction"])
    if ready_only:
        if before.is_ready:
            # Out of budget for the correction, but the held reading is still
            # searchable: say so rather than call it unavailable.
            return DisclosureEvidenceResult(
                state="ready", document_ref=ref, chunk_count=before.indexable_chunk_count,
                reused=True, notes=["searchable; a later correction at the source was "
                                    "not fetched (acquisition budget spent)"])
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_BUDGET_EXHAUSTED)

    source_type = _SOURCE_TYPES[document.source_id]
    try:
        url, host, why = await _content_url(document, cfg=cfg, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        url, host, why = None, None, type(exc).__name__
    if not url or not host:
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_PRIMARY_DOCUMENT_UNAVAILABLE,
                                        attempted=True, notes=[why or "no content address"])

    from app.services.sources.extracted_fact_validator import IssuerContext

    if extractor is None:
        from app.services.sources.live_fetchers import live_primary_document_extractor

        extractor = live_primary_document_extractor
    try:
        artifact = await extractor(
            url, allowed_domains=(host,), title_hint=_title(document),
            issuer_context=IssuerContext(company_name=issuer.name, ticker=issuer.ticker),
            cfg=cfg, period_policy=PERIOD_POLICY_TITLE_ONLY,
            published_at=document.published_on,
        )
    except Exception as exc:  # noqa: BLE001
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_PRIMARY_DOCUMENT_UNAVAILABLE,
                                        attempted=True, fetched=True,
                                        notes=[type(exc).__name__])

    # Provenance is the transport's, and the address a citation opens is the OFFICIAL
    # one (for ASX the announcement's own page, which serves this exact document).
    artifact.source_url = document.official_url
    artifact.title = _title(document)
    artifact.transport = document.source_id
    artifact.published_at = document.published_on
    artifact.period_policy = PERIOD_POLICY_TITLE_ONLY
    artifact.document_type = source_type
    artifact.doc_kind = document.doc_kind
    artifact.discovery_strategy = document.source_id

    if getattr(artifact, "status", None) != "extracted":
        await _record_attempt_safely(session, artifact, company_id=issuer.company_id,
                                     source_type=source_type, cfg=cfg)
        code = getattr(artifact, "failure_code", None) or "unknown"
        reason = (REASON_PRIMARY_DOCUMENT_UNAVAILABLE
                  if code.startswith(("blocked", "http", "fetch", "redirect", "unsupported",
                                      "client"))
                  else REASON_EXTRACTION_FAILED)
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref, reason=reason,
                                        attempted=True, fetched=True, notes=[code])

    if not document_names_issuer(artifact, issuer):
        # Not stored, not attributed: a document that does not name the issuer is not
        # the issuer's evidence, whatever the listing said.
        return DisclosureEvidenceResult(
            state="unavailable", document_ref=ref, reason=REASON_IDENTITY_UNVERIFIED,
            attempted=True, fetched=True,
            notes=["the fetched document does not name the issuer; nothing was stored"])

    from app.services.extracted_document_service import persist_primary_document_artifacts

    try:
        async with session.begin_nested():
            persisted = await persist_primary_document_artifacts(
                session, artifacts=[artifact], company_id=issuer.company_id,
                agent_run_id=None, cfg=cfg)
            await _record_attempt(session, artifact, company_id=issuer.company_id,
                                  source_type=source_type, cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("persisting a disclosure failed")
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_NOT_INDEXED, attempted=True,
                                        fetched=True, notes=[type(exc).__name__])
    # The same bytes may already be held for ANOTHER company (documents are deduplicated
    # by content hash, globally), and the corpus version then belongs to that company —
    # a dual-listed issuer's identical report, or a second row for the same issuer,
    # would never become READY here and would be re-fetched every run. This company
    # gets its own corpus version of the bytes it just read.
    await _ensure_company_version(session, artifact=artifact, company_id=issuer.company_id,
                                  ref=ref, cfg=cfg)
    try:
        async with session.begin_nested():
            indexed = await _index(session, company_id=issuer.company_id, ref=ref, cfg=cfg)
    except Exception:  # noqa: BLE001 - a failed index costs this document only
        indexed = 0

    try:
        after = await _state(session, issuer.company_id, ref)
    except Exception as exc:  # noqa: BLE001
        return DisclosureEvidenceResult(state="unavailable", document_ref=ref,
                                        reason=REASON_NOT_INDEXED, fetched=True,
                                        attempted=True, notes=[type(exc).__name__])
    notes = [f"documents created={persisted.documents_created} "
             f"reused={persisted.documents_reused} "
             f"corpus versions={persisted.corpus_versions_created} "
             f"chunks indexed={indexed}"]
    if after.is_ready:
        return DisclosureEvidenceResult(state="ready", document_ref=ref,
                                        chunk_count=after.indexable_chunk_count,
                                        fetched=True, attempted=True, notes=notes)
    return DisclosureEvidenceResult(
        state="unavailable", document_ref=ref, reason=REASON_NOT_INDEXED, fetched=True,
        attempted=True, notes=[*notes, after.detail or "no indexable chunks resulted"])


# ── Bounded selection ──────────────────────────────────────────────────────── #


def select_core_documents(
    listing: DisclosureListing, *, max_documents: int, now: datetime,
    topics: tuple[str, ...] = (),
) -> list[OfficialDocument]:
    """The documents research on this issuer cannot do without, in acquisition order:

    the latest annual report (within ~18 months), the latest interim report (12
    months), the latest periodic release (6 months), then recent material
    announcements (12 months) — newest first, topic matches first — up to the budget.
    Administrative notices are never chosen here.
    """
    from app.services.sources.disclosures.relevance import topic_boost

    dated = [(d, d.published_at) for d in listing.documents if d.published_at is not None]
    chosen: list[OfficialDocument] = []

    def newest(kind: str, days: int) -> OfficialDocument | None:
        window = now - timedelta(days=days)
        pool = [(d, at) for d, at in dated if d.doc_kind == kind and at >= window]
        return max(pool, key=lambda pair: pair[1])[0] if pool else None

    for kind, days in ((DOC_KIND_ANNUAL_REPORT, 550), (DOC_KIND_INTERIM_REPORT, 380),
                       (DOC_KIND_RESULTS_RELEASE, 190)):
        pick = newest(kind, days)
        if pick is not None:
            chosen.append(pick)
    window = now - timedelta(days=380)
    material = sorted(
        ((d, at) for d, at in dated if d.research_rank <= RANK_MATERIAL and d not in chosen
         and at >= window),
        key=lambda pair: (-topic_boost(pair[0].headline, topics), -pair[1].timestamp()),
    )
    chosen.extend(d for d, _at in material)
    return chosen[: max(0, int(max_documents))]


async def ensure_core_disclosures(
    session: Any, *, company: Any, cfg: Any, fetcher: Any = None, poster: Any = None,
    extractor: Any = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Secure an LSE / ASX issuer's core official documents in the corpus before its
    research questions are asked. Returns what happened; never raises."""
    out: dict[str, Any] = {"source_id": None, "documents": []}
    source_id, why = source_for(company, cfg)
    if source_id is None:
        out["skipped"] = why
        return out
    out["source_id"] = source_id
    now = now or datetime.now(timezone.utc)
    try:
        listing = await list_disclosures(session, company, cfg=cfg, fetcher=fetcher,
                                         poster=poster, now=now)
    except Exception as exc:  # noqa: BLE001
        out["skipped"] = f"listing failed ({type(exc).__name__})"
        return out
    out.update({"issuer": listing.issuer.to_dict() if listing.issuer else None,
                "listed": len(listing.documents), "refused": listing.refused,
                "requests": listing.requests})
    if listing.issuer is None or not listing.documents:
        out["skipped"] = listing.reason or REASON_DATA_NOT_SOURCED
        if listing.detail:
            out["detail"] = listing.detail
        return out
    selected = select_core_documents(
        listing, now=now,
        max_documents=int(getattr(cfg, "v3_disclosure_core_max_documents", 5) or 5))
    import time

    budget = float(getattr(cfg, "v3_disclosure_core_budget_seconds", 300) or 300)
    started = time.monotonic()
    for document in selected:
        # A wall budget for the whole step: each document is a fetch and an extraction
        # (an 80-page PDF can take a minute on a small host). Once spent, the rest are
        # answered from the database only — READY ones still count, nothing is fetched.
        spent = time.monotonic() - started >= budget
        result = await ensure_disclosure_evidence(
            session, issuer=listing.issuer, document=document, cfg=cfg, fetcher=fetcher,
            extractor=extractor, ready_only=spent)
        out["documents"].append({
            **document.to_item(), **result.to_dict(),
        })
    return out


__all__ = [
    "content_text",
    "document_names_issuer",
    "ensure_core_disclosures",
    "ensure_disclosure_evidence",
    "issuer_name_tokens",
    "list_disclosures",
    "select_core_documents",
    "source_for",
]
