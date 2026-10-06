"""Web documents into the Research Corpus — open-web W3 (spec §12.3).

THE ONE WRITE PATH
==================
Every fetched web document follows the same steps, whether it came from a search
result, a crawl, a user URL or a verified lead (``fetch_public_source``):

1. a ``web_fetch_attempts`` row (written by ``open_web_fetch``, or here for a lead);
2. refusals first — no bytes, a TDM reservation, a truncated body, an unsupported type
   are never ingested;
3. extraction AND analysis (SimHash, injection taint, entity mentions) in the killable
   process pool (``extract.py``) — nothing CPU-heavy runs on the event loop;
4. deterministic classification: source class → tier + access class, ``use_constraint``,
   document kind (``classify.py``);
5. duplicate check by content hash — the same bytes already stored (by the web path, or
   for the same company by any path) are LINKED with subject rows and re-indexed, never
   re-chunked; then (W4) by SimHash — nearly the same text already stored is LINKED the
   same way. The §14.2 origin (``trust.document_origin``: cluster, issuer, PR wire, wire
   attribution, boilerplate, cross-domain canonical, publisher group, domain) is stored
   as ``origin_key``;
6. raw bytes → the artifact store under the version's access class, with the web TTL
   (``V3_WEB_ARTIFACT_RETENTION_DAYS``) when the constraint is ``unknown``;
7. ``ExtractedDocument`` — SHARED by content hash, so its ``company_id`` is whoever
   extracted it first and is never read as this run's company;
8. ``ingest_extracted_document`` → a version with ``access_class``,
   ``transport="open_web:<provider|direct|user>"``, ``content_origin``, tier and the
   W3 fields, then derivation, pages, sections, tables and chunks (``ev:c:`` ids);
9. ``research_document_subjects`` rows: the run's company (``primary``), mentioned
   companies, brand scopes, and the THEME the document was acquired for;
10. indexing, so the chunks are retrievable.

PREPARE, THEN STORE
===================
:func:`prepare_web_document` does every slow thing (pool extraction, classification)
and writes NOTHING; :func:`store_web_document` only writes. A caller inside a research
transaction (the verified-lead path) prepares first and opens its SAVEPOINT only around
the store (review F6), so a 120 s PDF never holds a database transaction open.

DATES (review F9)
=================
A date htmldate found in the page TEXT (``published_at_source='text'``) is stored on the
version for the ``since`` filter, labelled, but never handed to the period rules: it
can be an unrelated earlier date, and a title period checked against it would be
nulled wrongly.

FLAG
====
``V3_WEB_CORPUS_INGEST_ENABLED`` (default off) — this module is its only consumer — and
``V3_CORPUS_ENABLED``. Off: ``state="disabled"``, no extraction, no query, no row.

Logs carry codes, counts and a 12-character URL hash — never a URL, never page text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from app.core.structured_logging import log_event

logger = logging.getLogger(__name__)

STATE_INGESTED = "ingested"
STATE_REUSED = "reused"
STATE_NOT_INGESTED = "not_ingested"
STATE_DISABLED = "disabled"

REASON_DISABLED = "web_ingest_disabled"
REASON_NO_CONTENT = "no_content"
REASON_TDM_RESERVED = "tdm_reserved"
REASON_TRUNCATED = "truncated_body"
REASON_UNSUPPORTED = "unsupported_type"
REASON_NO_SUBJECT = "no_subject"
REASON_EXTRACTION = "extraction_failed"

SUBJECT_SCOPES: frozenset[str] = frozenset({"company", "theme", "industry", "macro"})
SOURCE_TYPE_OPEN_WEB = "open_web"
TRANSPORT_PREFIX = "open_web:"
DATE_SOURCE_TEXT = "text"
DATE_SOURCE_PDF_METADATA = "pdf_metadata"
NON_AUTHORITATIVE_DATES: frozenset[str] = frozenset(
    {DATE_SOURCE_TEXT, DATE_SOURCE_PDF_METADATA}
)

_TRANSPORT_MAX = 100
_THEME_KEY_MAX = 120


@dataclass
class WebIngestResult:
    """What one ingestion did. Secret-free; safe to log and to return to a tool."""

    state: str
    reason: str | None = None
    version_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    extracted_document_id: uuid.UUID | None = None
    source_class: str | None = None
    tier: str | None = None
    access_class: str | None = None
    use_constraint: str | None = None
    document_kind: str | None = None
    injection_suspect: bool = False
    injection_signals: tuple[str, ...] = ()
    subjects_written: int = 0
    chunks: int = 0
    indexed: int = 0
    extraction_method: str | None = None
    extraction_confidence: str | None = None
    stopped_by: str | None = None
    published_at_source: str | None = None
    #: Open-web W4: the §14.2 origin and the rule that decided it.
    origin_key: str | None = None
    origin_rule: str | None = None
    #: Open-web W5: pages the extractor read (PDF page count; 0 for HTML/text).
    pages: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def stored(self) -> bool:
        return self.state in (STATE_INGESTED, STATE_REUSED) and self.version_id is not None


@dataclass
class PreparedWebDocument:
    """Everything decided about one document BEFORE any database write."""

    fetched: Any
    url: str
    canonical: str
    host: str
    content_hash: str
    extraction: Any
    classification: Any
    kind: str
    scope: str
    company_id: uuid.UUID | None
    theme_key: str | None
    provider: str | None
    result: WebIngestResult
    #: Open-web W4: the subject issuer (``trust.IssuerIdentity``) for the origin rules.
    issuer: Any = None
    #: The raw bytes as already PUT in the artifact store (hash-addressed, idempotent),
    #: so ``store_web_document`` — which runs inside the caller's savepoint — does no
    #: network upload (review F6). None when the corpus is off or this is a duplicate.
    stored_artifact: Any = None


def ingest_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_web_corpus_ingest_enabled", False)) and bool(
        getattr(cfg, "v3_corpus_enabled", False)
    )


def transport_for(provider: str | None) -> str:
    """``open_web:<provider|direct|user>`` — WHO delivered the bytes, never a tier."""
    label = "".join(ch for ch in (provider or "direct").lower() if ch.isalnum() or ch in "_-.")
    return f"{TRANSPORT_PREFIX}{label or 'direct'}"[:_TRANSPORT_MAX]


def _url_hash(url: str | None) -> str:
    return hashlib.sha256((url or "").encode("utf-8", "replace")).hexdigest()[:12]


def _host(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _retention_days(cfg: Any, use_constraint: str) -> int:
    if use_constraint == "unknown":
        return int(getattr(cfg, "v3_web_artifact_retention_days", 30) or 0)
    return int(getattr(cfg, "v3_artifact_retention_days", 0) or 0)


def _refusal(fetched: Any) -> str | None:
    from app.services.web_research import content as content_mod
    from app.services.web_research import robots

    if getattr(fetched, "tdm_decision", None) == robots.TDM_RESERVED or (
        getattr(fetched, "failure_code", None) == "tdm_reserved"
    ):
        return REASON_TDM_RESERVED
    if getattr(fetched, "content", None) is None or not getattr(fetched, "ok", False):
        return getattr(fetched, "failure_code", None) or REASON_NO_CONTENT
    if not getattr(fetched, "complete", False):
        # A body cut at its cap is never presented as the whole document.
        return REASON_TRUNCATED
    if getattr(fetched, "content_class", None) not in (
        content_mod.CLASS_HTML, content_mod.CLASS_PDF, content_mod.CLASS_TEXT,
    ):
        return REASON_UNSUPPORTED
    return None


# --------------------------------------------------------------------------- #
# Prepare (slow, writes nothing)
# --------------------------------------------------------------------------- #


async def prepare_web_document(
    fetched: Any,
    *,
    cfg: Any,
    provider: str | None = None,
    company_id: uuid.UUID | None = None,
    subject_scope: str | None = None,
    theme_key: str | None = None,
    candidates: tuple[Any, ...] | list[Any] = (),
    issuer_domains: tuple[str, ...] = (),
    query_terms: tuple[str, ...] = (),
    depth: str = "standard",
    pool: Any = None,
    store: Any = None,
    now: datetime | None = None,
) -> PreparedWebDocument | WebIngestResult:
    """Extract, analyse and classify one fetched document, and PUT its raw bytes in the
    artifact store. No database access at all.

    Returns a :class:`WebIngestResult` when the document is refused or did not extract.
    """
    if not ingest_enabled(cfg):
        return WebIngestResult(STATE_DISABLED, REASON_DISABLED)
    refusal = _refusal(fetched)
    if refusal:
        return WebIngestResult(STATE_NOT_INGESTED, refusal)
    scope = (subject_scope or ("company" if company_id else "")).strip().lower()
    if scope not in SUBJECT_SCOPES or (scope == "company" and company_id is None):
        return WebIngestResult(STATE_NOT_INGESTED, REASON_NO_SUBJECT)
    if company_id is not None and scope != "company":
        scope = "company"

    from app.services.web_research.classify import classify_document_kind, classify_source
    from app.services.web_research.extract import extract_web_document

    url = fetched.final_url or fetched.requested_url
    canonical = fetched.canonical_url or url
    host = _host(url)
    content_hash = fetched.content_hash or hashlib.sha256(fetched.content).hexdigest()

    extraction = await extract_web_document(
        raw=fetched.content,
        content_class=fetched.content_class,
        cfg=cfg,
        url=url,
        charset=fetched.charset,
        js_required=bool(getattr(fetched, "js_required", False)),
        query_terms=tuple(query_terms),
        depth=depth,
        pool=pool,
        candidates=tuple(candidates),
    )
    meta = extraction.metadata
    classification = classify_source(
        url, issuer_domains=tuple(issuer_domains), licence_signals=meta.licence_signals
    )
    kind = classify_document_kind(
        url=url,
        title=meta.title,
        source_class=classification.source_class,
        content_class=fetched.content_class,
        jsonld_types=meta.jsonld_types,
        og_type=meta.og_type,
        has_citation_meta=meta.has_citation_meta,
        headings=meta.headings,
    )
    result = WebIngestResult(
        state=STATE_NOT_INGESTED,
        source_class=classification.source_class,
        tier=classification.tier,
        access_class=classification.access_class,
        use_constraint=classification.use_constraint,
        document_kind=kind,
        injection_suspect=extraction.injection_suspect,
        injection_signals=tuple(extraction.injection_signals),
        extraction_method=extraction.method,
        extraction_confidence=extraction.confidence,
        stopped_by=extraction.stopped_by,
        published_at_source=meta.published_at_source,
        pages=int(extraction.page_count or 0),
    )
    if not extraction.extracted:
        result.reason = extraction.failure_code or REASON_EXTRACTION
        _log(result, url)
        return result
    stamp = now or datetime.now(timezone.utc)
    stored_artifact = await _put_artifact(
        fetched, classification, cfg=cfg, store=store, stamp=stamp
    )
    return PreparedWebDocument(
        stored_artifact=stored_artifact,
        fetched=fetched,
        url=url,
        canonical=canonical,
        host=host,
        content_hash=content_hash,
        extraction=extraction,
        classification=classification,
        kind=kind,
        scope=scope,
        company_id=company_id,
        theme_key=((theme_key or "").strip()[:_THEME_KEY_MAX] or None)
        if company_id is None else None,
        provider=provider,
        result=result,
        issuer=_issuer_of(company_id, candidates, issuer_domains),
    )


def _issuer_of(company_id: Any, candidates: Any, issuer_domains: Any) -> Any:
    from app.services.web_research.trust import issuer_from_candidates

    return issuer_from_candidates(company_id, candidates, tuple(issuer_domains or ()))


async def _put_artifact(
    fetched: Any, classification: Any, *, cfg: Any, store: Any, stamp: datetime
) -> Any:
    """Retain the raw bytes where policy permits. Content-addressed, so a duplicate or
    a retry is a no-op; a failure is a recorded outcome, never an exception."""
    from app.services.corpus.artifacts.service import store_raw_artifact
    from app.services.corpus.policy import default_policy_for

    policy = default_policy_for(
        classification.access_class,
        retention_days=_retention_days(cfg, classification.use_constraint),
        now=stamp,
    )
    media_type = _MEDIA_TYPES.get(
        fetched.content_class, fetched.mime_sniffed or "application/octet-stream"
    )
    try:
        return await store_raw_artifact(
            fetched.content,
            media_type=media_type,
            access_class=classification.access_class,
            cfg=cfg,
            store=store,
            policy=policy,
            now=stamp,
        )
    except Exception:  # noqa: BLE001 - losing the bytes costs re-extraction, not the document
        logger.warning("web artifact could not be stored")
        return None


# --------------------------------------------------------------------------- #
# Store (writes only)
# --------------------------------------------------------------------------- #


async def store_web_document(
    session: Any,
    prepared: PreparedWebDocument,
    *,
    cfg: Any,
    store: Any = None,
    backend: Any = None,
    now: datetime | None = None,
) -> WebIngestResult:
    """Write one prepared document: link a duplicate, or store a new version."""
    result = prepared.result
    extraction = prepared.extraction
    meta = extraction.metadata
    classification = prepared.classification
    company_id = prepared.company_id
    stamp = now or datetime.now(timezone.utc)
    mentions = list(extraction.mentions)

    # Same bytes already in the corpus: LINK, never re-chunk (spec §14.1, review F5).
    existing = await linked_version(session, prepared.content_hash, company_id=company_id)
    if existing is not None:
        result.state = STATE_REUSED
        result.version_id = existing.id
        result.document_id = existing.research_document_id
        result.extracted_document_id = existing.extracted_document_id
        attempt = getattr(prepared.fetched, "attempt_id", None)
        if existing.web_fetch_attempt_id is None and attempt and (
            existing.web_extractor_version is not None
        ):
            existing.web_fetch_attempt_id = attempt
        result.subjects_written = await write_subjects(
            session,
            document_id=existing.research_document_id,
            version_id=existing.id,
            company_id=company_id,
            mentions=mentions,
            theme_key=prepared.theme_key,
        )
        if result.subjects_written:
            # The memory backend captures subjects at index time; re-index so it agrees
            # with PostgreSQL, which reads them at query time (review F4).
            result.indexed = await _index(session, existing.id, cfg=cfg, backend=backend)
        result.notes.append("same bytes already stored; linked, not re-chunked")
        _log(result, prepared.url)
        return result

    # Open-web W4 (spec §14.1–14.2): the origin algorithm, and a near-duplicate of a
    # stored document is LINKED to it rather than chunked again.
    from app.services.web_research.dedup import apply_stricter_use_constraint
    from app.services.web_research.trust import OriginInput, document_origin

    decision, duplicate = await document_origin(
        session,
        doc=OriginInput(
            url=prepared.url,
            text=extraction.main_text,
            source_class=classification.source_class,
            rel_canonical=getattr(prepared.fetched, "rel_canonical", None),
            company_id=company_id,
        ),
        simhash=extraction.simhash,
        text=extraction.main_text,
        company_id=company_id,
        theme_key=prepared.theme_key,
        issuer=prepared.issuer,
    )
    result.origin_key = decision.origin_key
    result.origin_rule = decision.rule
    if duplicate is not None:
        result.state = STATE_REUSED
        result.version_id = duplicate.id
        result.document_id = duplicate.research_document_id
        result.extracted_document_id = duplicate.extracted_document_id
        result.subjects_written = await write_subjects(
            session,
            document_id=duplicate.research_document_id,
            version_id=duplicate.id,
            company_id=company_id,
            mentions=mentions,
            theme_key=prepared.theme_key,
        )
        if result.subjects_written:
            result.indexed = await _index(session, duplicate.id, cfg=cfg, backend=backend)
        # Linking must not loosen a licence: keep the stricter use_constraint (review M7).
        stricter = await apply_stricter_use_constraint(
            session, duplicate, classification.use_constraint
        )
        if stricter:
            result.notes.append(f"use_constraint tightened to {stricter} on link")
        result.notes.append("near-duplicate of a stored document; linked, not re-chunked")
        _log(result, prepared.url)
        return result

    from app.services.corpus.artifacts.service import record_artifact
    from app.services.corpus.documents import (
        CorpusIngestResult,
        WebVersionFields,
        ingest_extracted_document,
    )
    from app.services.sources.disclosure_period_policy import PERIOD_POLICY_TITLE_ONLY
    from app.services.web_research.dedup import origin_key_for
    from app.services.web_research.extract import WEB_EXTRACTOR_VERSION

    fetched = prepared.fetched
    media_type = _MEDIA_TYPES.get(
        fetched.content_class, fetched.mime_sniffed or "application/octet-stream"
    )
    stored = prepared.stored_artifact  # already PUT during prepare: no upload here
    await record_artifact(session, stored, cfg=cfg, now=stamp)

    body = extraction.extraction
    title = (meta.title or "")[:500] or None
    transport = transport_for(prepared.provider)
    # A text-found date and a PDF /CreationDate (often inherited from last year's file)
    # are not authoritative: neither reaches the period rules (review F9).
    authoritative_date = (
        meta.published_at if meta.published_at_source not in NON_AUTHORITATIVE_DATES
        else None
    )
    document = await _get_or_create_extracted_document(
        session,
        content_hash=prepared.content_hash,
        canonical_url=prepared.canonical,
        transport=transport,
        tier=classification.tier,
        mime_type=media_type,
        title=title,
        published_at=authoritative_date,
        body=body,
        company_id=company_id,
        blob_path=getattr(stored, "storage_key", None),
        now=stamp,
    )
    result.extracted_document_id = document.id

    # The shared V2 row, seen as THIS retrieval: this run's company (never the first
    # extractor's), this address, this transport.
    view = SimpleNamespace(
        id=document.id,
        content_hash=prepared.content_hash,
        canonical_url=prepared.canonical,
        provider=transport,
        source_tier=classification.tier,
        company_id=company_id,
        source_type=SOURCE_TYPE_OPEN_WEB,
        title=title,
        mime_type=media_type,
        doc_date=authoritative_date,
        retrieved_at=stamp,
        status=body.status,
    )
    artifact = SimpleNamespace(
        raw_artifact=stored,
        extraction=body,
        title=title,
        source_url=prepared.canonical,
        doc_kind=prepared.kind,
        period_policy=PERIOD_POLICY_TITLE_ONLY,
        failure_code=None,
    )
    origin = decision.origin_key
    web = WebVersionFields(
        web_fetch_attempt_id=getattr(fetched, "attempt_id", None),
        use_constraint=classification.use_constraint,
        injection_suspect=extraction.injection_suspect,
        simhash=extraction.simhash,
        origin_key=origin,
        published_at_source=meta.published_at_source if meta.published_at else None,
        source_class=classification.source_class,
        web_extractor_version=WEB_EXTRACTOR_VERSION,
        subject_scope=prepared.scope,
        # The PUBLISHER (where the bytes were served); ``origin_key`` is who wrote them.
        content_origin=origin_key_for(prepared.host),
        published_at=meta.published_at,
    )
    counts = CorpusIngestResult()
    version = await ingest_extracted_document(
        session, artifact=artifact, document=view,  # type: ignore[arg-type]
        cfg=cfg, now=stamp, result=counts, web=web,
    )
    if version is None:
        result.reason = "corpus_version_not_written"
        _log(result, prepared.url)
        return result
    result.state = STATE_INGESTED if counts.versions_created else STATE_REUSED
    result.version_id = version.id
    result.document_id = version.research_document_id
    result.chunks = await _chunk_count(session, version.id)
    result.subjects_written = await write_subjects(
        session,
        document_id=version.research_document_id,
        version_id=version.id,
        company_id=company_id,
        mentions=mentions,
        theme_key=prepared.theme_key,
    )
    if company_id is not None and counts.versions_created:
        await _scope_brand_only_page(session, version.id, company_id, mentions)
    result.indexed = await _index(session, version.id, cfg=cfg, backend=backend)
    _log(result, prepared.url)
    return result


async def ingest_web_document(
    session: Any,
    fetched: Any,
    *,
    cfg: Any,
    provider: str | None = None,
    company_id: uuid.UUID | None = None,
    subject_scope: str | None = None,
    theme_key: str | None = None,
    candidates: tuple[Any, ...] | list[Any] = (),
    issuer_domains: tuple[str, ...] = (),
    query_terms: tuple[str, ...] = (),
    depth: str = "standard",
    pool: Any = None,
    store: Any = None,
    backend: Any = None,
    now: datetime | None = None,
) -> WebIngestResult:
    """Prepare then store one fetched document (an ``OpenWebFetchResult``).

    ``company_id`` makes it a company document (``subject_scope="company"``). Without
    one, ``subject_scope`` (``theme | industry | macro``) is required; the document is
    company-less and deduplicated by the partial unique index on its key; ``theme_key``
    links it to a theme through a subject row. Never raises for a document-level
    outcome; a database error propagates to the caller's savepoint.
    """
    prepared = await prepare_web_document(
        fetched,
        cfg=cfg,
        provider=provider,
        company_id=company_id,
        subject_scope=subject_scope,
        theme_key=theme_key,
        candidates=candidates,
        issuer_domains=issuer_domains,
        query_terms=query_terms,
        depth=depth,
        pool=pool,
        store=store,
        now=now,
    )
    if isinstance(prepared, WebIngestResult):
        return prepared
    return await store_web_document(
        session, prepared, cfg=cfg, store=store, backend=backend, now=now
    )


_MEDIA_TYPES = {"pdf": "application/pdf", "html": "text/html", "text": "text/plain"}


async def linked_version(
    session: Any, content_hash: str | None, *, company_id: uuid.UUID | None
) -> Any:
    """The stored version these bytes should be LINKED to, or None (review F5).

    In order: a version of THIS company's own document with the same bytes, by any path
    (an issuer PDF the filing pipeline already holds must not be stored twice and
    double-count as corroboration); else the earliest WEB version of the bytes; else,
    for a company-less run, the earliest version of any kind.
    """
    if not content_hash:
        return None
    from sqlalchemy import select

    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    V = ResearchDocumentVersion
    rows = (
        await session.execute(
            select(V, ResearchDocument.company_id)
            .join(ResearchDocument, ResearchDocument.id == V.research_document_id)
            .where(V.content_hash == content_hash)
            .order_by(V.created_at, V.id)
            .limit(50)
        )
    ).all()
    if not rows:
        return None
    if company_id is not None:
        own = [v for v, owner in rows if owner == company_id]
        if own:
            return own[0]
    web = [v for v, _owner in rows if v.web_extractor_version is not None]
    if web:
        return web[0]
    return rows[0][0] if company_id is None else None


async def _get_or_create_extracted_document(
    session: Any,
    *,
    content_hash: str,
    canonical_url: str,
    transport: str,
    tier: str,
    mime_type: str,
    title: str | None,
    published_at: Any,
    body: Any,
    company_id: uuid.UUID | None,
    blob_path: str | None,
    now: datetime,
) -> Any:
    """The shared V2 row for these bytes. ``company_id`` is set on CREATION only."""
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument
    from app.services.extracted_document_service import _excerpts_to_json
    from app.services.sources.extraction_pipeline_version import (
        CURRENT_EXTRACTION_PIPELINE_VERSION,
    )
    from app.services.sources.redaction import canonicalize_source_url

    existing = (
        await session.execute(
            select(ExtractedDocument)
            .where(ExtractedDocument.content_hash == content_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if blob_path and not existing.blob_path:
            existing.blob_path = blob_path
        return existing
    row = ExtractedDocument(
        id=uuid.uuid4(),
        content_hash=content_hash,
        canonical_url=(canonicalize_source_url(canonical_url) or canonical_url)[:2000],
        provider=transport,
        source_type=SOURCE_TYPE_OPEN_WEB,
        source_tier=tier,
        mime_type=mime_type[:100],
        title=title,
        doc_date=published_at,
        retrieved_at=now,
        extraction_method=(body.extraction_method or "html")[:50],
        page_count=body.page_count,
        status=body.status,
        excerpts_json=_excerpts_to_json(body),
        pipeline_version=CURRENT_EXTRACTION_PIPELINE_VERSION,
        company_id=company_id,
        blob_path=blob_path,
    )
    session.add(row)
    await session.flush()
    return row


def _brand_only_scope(company_id: uuid.UUID | None, mentions: list[Any]) -> str | None:
    """The brand scope when the run's company appears ONLY through a brand (review F11)."""
    if company_id is None:
        return None
    direct = [m for m in mentions if m.company_id == company_id and m.scope_key is None]
    if direct:
        return None
    brands = sorted(
        m.scope_key for m in mentions if m.company_id == company_id and m.scope_key
    )
    return brands[0] if brands else None


async def write_subjects(
    session: Any,
    *,
    document_id: uuid.UUID,
    version_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    mentions: list[Any],
    theme_key: str | None = None,
) -> int:
    """``research_document_subjects`` rows for one document. Idempotent and race-safe.

    The run's own company is the ``primary`` subject (with the confidence the text
    earned, or none when the text never names it; with the brand's scope when the text
    names it only through a brand). Every other mention is ``mentioned``. A brand
    mention carries its ``segment:`` / ``brand:`` scope — never ``group``. A
    ``name_only`` match is recorded for a LEAD only. ``theme_key`` adds a
    ``relation='theme'`` row. Inserts are ``ON CONFLICT DO NOTHING`` against the unique
    row-identity index (review F10), so concurrent writers never duplicate a row.
    """
    from app.models.research_document import ResearchDocumentSubject
    from app.services.web_research.entities import CONF_NAME_ONLY

    primary_mention = next(
        (
            m
            for m in mentions
            if company_id is not None and m.company_id == company_id and m.scope_key is None
        ),
        None,
    )
    planned: list[dict[str, Any]] = []
    if company_id is not None:
        planned.append(
            {
                "company_id": company_id,
                "legal_entity_id": getattr(primary_mention, "legal_entity_id", None),
                "relation": "primary",
                "confidence": getattr(primary_mention, "confidence", None),
                "method": getattr(primary_mention, "method", None) or "research_run",
                "scope_key": _brand_only_scope(company_id, mentions),
                "surface": getattr(primary_mention, "surface", None),
            }
        )
    for mention in mentions:
        if mention is primary_mention:
            continue
        if mention.company_id is None and mention.legal_entity_id is None:
            continue
        if mention.confidence == CONF_NAME_ONLY and not mention.candidate.is_lead:
            continue
        if mention.scope_key and not mention.scope_key.startswith(("segment:", "brand:")):
            continue  # a brand never becomes a group fact (spec §16.2)
        planned.append(
            {
                "company_id": mention.company_id,
                "legal_entity_id": mention.legal_entity_id,
                "relation": "mentioned",
                "confidence": mention.confidence,
                "method": mention.method,
                "scope_key": mention.scope_key,
                "surface": mention.surface,
            }
        )
    if theme_key:
        planned.append(
            {
                "company_id": None,
                "legal_entity_id": None,
                "relation": "theme",
                "confidence": None,
                "method": "research_run",
                "scope_key": None,
                "theme_key": theme_key,
                "surface": None,
            }
        )
    if not planned:
        return 0
    chunks = await _folded_chunks(session, version_id)
    surfaces = [p["surface"] for p in planned]
    evidence = await asyncio.to_thread(_evidence_chunks, chunks, surfaces)
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    if getattr(dialect, "name", "") == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert  # type: ignore[assignment]
    written = 0
    for item, chunk_id in zip(planned, evidence, strict=True):
        values = {
            "id": uuid.uuid4(),
            "research_document_id": document_id,
            "company_id": item["company_id"],
            "legal_entity_id": item["legal_entity_id"],
            "relation": item["relation"],
            "confidence": item["confidence"],
            "method": (item["method"] or "research_run")[:40],
            "scope_key": item["scope_key"],
            "theme_key": item.get("theme_key"),
            "evidence_chunk_id": chunk_id,
            "created_at": datetime.now(timezone.utc),
        }
        outcome = await session.execute(
            dialect_insert(ResearchDocumentSubject)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(ResearchDocumentSubject.id)
        )
        # RETURNING, not rowcount: the async PostgreSQL driver reports -1 for it.
        written += len(outcome.fetchall())
    return written


async def _folded_chunks(session: Any, version_id: uuid.UUID | None) -> list[tuple[str, str]]:
    if version_id is None:
        return []
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk

    rows = (
        await session.execute(
            select(ResearchDocumentChunk.chunk_id, ResearchDocumentChunk.text)
            .where(ResearchDocumentChunk.research_document_version_id == version_id)
            .order_by(ResearchDocumentChunk.ordinal)
            .limit(2000)
        )
    ).all()
    return [(str(cid), str(text or "")) for cid, text in rows]


def _evidence_chunks(
    chunks: list[tuple[str, str]], surfaces: list[str | None]
) -> list[str | None]:
    """The first chunk each surface occurs in. Each chunk is folded ONCE (review F8)."""
    from app.services.web_research.entities import fold

    folded = [(chunk_id, fold(text)) for chunk_id, text in chunks]
    out: list[str | None] = []
    for surface in surfaces:
        needle = fold(surface) if surface else ""
        found = None
        if needle:
            for chunk_id, text in folded:
                if needle in text:
                    found = chunk_id[:120]
                    break
        out.append(found)
    return out


async def _scope_brand_only_page(
    session: Any, version_id: uuid.UUID, company_id: uuid.UUID, mentions: list[Any]
) -> None:
    """A page naming the company only through a brand: its unscoped chunks take the
    brand's scope, so a Cartier headline can never fill a Richemont Group slot
    (review F11)."""
    scope_key = _brand_only_scope(company_id, mentions)
    if not scope_key:
        return
    from sqlalchemy import update

    from app.models.research_chunk import ResearchDocumentChunk

    _kind, _sep, name = scope_key.partition(":")
    await session.execute(
        update(ResearchDocumentChunk)
        .where(
            ResearchDocumentChunk.research_document_version_id == version_id,
            ResearchDocumentChunk.scope_key.is_(None),
        )
        .values(scope_type="segment", scope_name=name[:200] or None, scope_key=scope_key)
    )
    await session.flush()


async def _chunk_count(session: Any, version_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from app.models.research_chunk import ResearchDocumentChunk

    value = (
        await session.execute(
            select(func.count())
            .select_from(ResearchDocumentChunk)
            .where(ResearchDocumentChunk.research_document_version_id == version_id)
        )
    ).scalar_one()
    return int(value or 0)


async def _index(session: Any, version_id: uuid.UUID, *, cfg: Any, backend: Any) -> int:
    """Make the chunks retrievable. A failed index costs this document's retrievability."""
    from app.services.corpus.indexing import index_version

    try:
        if backend is None:
            from app.services.corpus.search.factory import get_search_backend

            backend = get_search_backend(cfg, session=session)
        if backend is None:
            return 0
        async with session.begin_nested():
            outcome = await index_version(
                session, research_document_version_id=version_id, backend=backend, cfg=cfg
            )
        return int(outcome.indexed) + int(outcome.updated)
    except Exception:  # noqa: BLE001 - indexing never fails an ingestion
        logger.warning("web document indexing failed")
        return 0


def _log(result: WebIngestResult, url: str | None) -> None:
    log_event(
        logger,
        "web_document_ingest",
        state=result.state,
        reason=result.reason,
        source_class=result.source_class,
        document_kind=result.document_kind,
        use_constraint=result.use_constraint,
        injection_suspect=result.injection_suspect,
        extraction_method=result.extraction_method,
        stopped_by=result.stopped_by,
        chunks=result.chunks,
        subjects=result.subjects_written,
        url_hash=_url_hash(url),
    )


# --------------------------------------------------------------------------- #
# The verified-lead path (fetch_public_source → verify_lead)
# --------------------------------------------------------------------------- #


class _RowCollector:
    """Stands in for a session while robots/TDMRep are checked OUTSIDE the savepoint:
    the attempt rows are kept and written later, inside it (review F6)."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None


@dataclass
class PreparedLeadDocument:
    rows: list[Any]
    prepared: PreparedWebDocument


async def prepare_verified_lead_document(
    *,
    content: bytes | None,
    url: str,
    fetched_url: str | None,
    truncated: bool,
    cfg: Any,
    company_id: uuid.UUID | None,
    headers: dict[str, str] | None = None,
    research_job_id: uuid.UUID | None = None,
    web_search_result_id: uuid.UUID | None = None,
    provider: str | None = None,
    candidates: tuple[Any, ...] = (),
    pool: Any = None,
    store: Any = None,
    resolver: Any = None,
    runtime: Any = None,
) -> PreparedLeadDocument | WebIngestResult:
    """Everything slow for a VERIFIED lead's bytes — no database write (review F6).

    ``verify_lead`` fetches through ``safe_fetch_document``, which predates the open-web
    policy, so the same refusals ``open_web_fetch`` applies are applied here before
    anything is stored (review S-M4): the origin's robots.txt / TDMRep
    (``fetch.ingestion_clearance``), the response's TDM / ``noai`` HEADERS, the page's
    TDM meta, and the access-wall verdict (login, consent, CAPTCHA, a paywall's
    ``isAccessibleForFree: false``).
    """
    if not ingest_enabled(cfg):
        return WebIngestResult(STATE_DISABLED, REASON_DISABLED)
    if not content:
        return WebIngestResult(STATE_NOT_INGESTED, REASON_NO_CONTENT)
    if truncated:
        return WebIngestResult(STATE_NOT_INGESTED, REASON_TRUNCATED)
    if company_id is None:
        return WebIngestResult(STATE_NOT_INGESTED, REASON_NO_SUBJECT)

    from app.models.web_research import WebFetchAttempt
    from app.services.sources.safe_web_fetcher import SNIFF_PREFIX_BYTES
    from app.services.web_research import content as content_mod
    from app.services.web_research import robots
    from app.services.web_research.canonical import canonical_url, stored_url
    from app.services.web_research.fetch import (
        ORIGIN_LEAD,
        POLICY_ALLOWED,
        STATUS_FETCHED,
        OpenWebFetchResult,
        WebFetchContext,
        ingestion_clearance,
    )

    final = fetched_url or url
    if robots.tdm_signals_from_headers({k.lower(): v for k, v in (headers or {}).items()}):
        return WebIngestResult(STATE_NOT_INGESTED, REASON_TDM_RESERVED)
    collector = _RowCollector()
    clearance = await ingestion_clearance(
        final,
        session=collector,
        context=WebFetchContext(research_job_id=research_job_id),
        cfg=cfg,
        resolver=resolver,
        runtime=runtime,
    )
    if not clearance.allowed:
        return WebIngestResult(STATE_NOT_INGESTED, clearance.failure_code or REASON_TDM_RESERVED)
    sniffed = content_mod.sniff(content[:SNIFF_PREFIX_BYTES])
    if not sniffed.supported:
        return WebIngestResult(STATE_NOT_INGESTED, REASON_UNSUPPORTED)
    charset, _source = content_mod.detect_charset(
        content, content_type=None, content_class=sniffed.content_class
    )
    js_required = False
    if sniffed.content_class == content_mod.CLASS_HTML:
        verdict = await asyncio.to_thread(
            _html_gate, content, charset, url, final
        )
        if verdict is not None:
            return WebIngestResult(STATE_NOT_INGESTED, verdict)
        html = content_mod.decode_text(content, charset)
        js_required = content_mod.js_required(len(content), html)

    content_hash = hashlib.sha256(content).hexdigest()
    stored_final = stored_url(final) or final
    canonical = canonical_url(final) or stored_final
    attempt_id = uuid.uuid4()
    attempt = WebFetchAttempt(
        id=attempt_id,
        research_job_id=research_job_id,
        web_search_result_id=web_search_result_id,
        origin=ORIGIN_LEAD,
        requested_url=(stored_url(url) or url)[:2048],
        final_url=stored_final[:2048],
        canonical_url=canonical[:2048],
        policy_decision=POLICY_ALLOWED,
        robots_decision=clearance.robots_decision,
        tdm_decision=clearance.tdm_decision,
        mime_sniffed=sniffed.mime,
        bytes=len(content),
        truncated=False,
        content_hash=content_hash,
        status=STATUS_FETCHED,
        created_at=datetime.now(timezone.utc),
    )
    fetched = OpenWebFetchResult(
        status=STATUS_FETCHED,
        origin=ORIGIN_LEAD,
        requested_url=stored_url(url) or url,
        attempt_id=attempt_id,
        final_url=stored_final,
        canonical_url=canonical,
        robots_decision=clearance.robots_decision,
        tdm_decision=clearance.tdm_decision,
        mime_sniffed=sniffed.mime,
        content_class=sniffed.content_class,
        content=content,
        bytes=len(content),
        content_hash=content_hash,
        charset=charset,
        js_required=js_required,
    )
    prepared = await prepare_web_document(
        fetched,
        cfg=cfg,
        provider=provider or "lead",
        company_id=company_id,
        candidates=candidates,
        pool=pool,
        store=store,
    )
    if isinstance(prepared, WebIngestResult):
        return prepared
    return PreparedLeadDocument(rows=[*collector.rows, attempt], prepared=prepared)


def _html_gate(content: bytes, charset: str, url: str, final: str) -> str | None:
    """The refusal code for an HTML page that must not be ingested, or None."""
    from app.services.web_research import access, robots
    from app.services.web_research import content as content_mod

    html = content_mod.decode_text(content, charset)
    if robots.tdm_signals_from_html(html):
        return REASON_TDM_RESERVED
    visible: list[str] = []

    def _visible() -> str:
        if not visible:
            visible.append(content_mod.visible_text(html))
        return visible[0]

    verdict = access.classify_page(
        html, visible_text=_visible, requested_url=url, final_url=final
    )
    return None if verdict.retrievable else (verdict.reason or "access_wall")


async def store_verified_lead_document(
    session: Any,
    prepared: PreparedLeadDocument,
    *,
    cfg: Any,
    store: Any = None,
    backend: Any = None,
) -> WebIngestResult:
    """The writes for a prepared lead document — the only part inside a savepoint."""
    for row in prepared.rows:
        session.add(row)
    await session.flush()
    return await store_web_document(
        session, prepared.prepared, cfg=cfg, store=store, backend=backend
    )


async def ingest_verified_lead_document(
    session: Any,
    *,
    content: bytes | None,
    url: str,
    fetched_url: str | None,
    truncated: bool,
    cfg: Any,
    company_id: uuid.UUID | None,
    headers: dict[str, str] | None = None,
    research_job_id: uuid.UUID | None = None,
    web_search_result_id: uuid.UUID | None = None,
    provider: str | None = None,
    candidates: tuple[Any, ...] = (),
    pool: Any = None,
    store: Any = None,
    backend: Any = None,
    resolver: Any = None,
    runtime: Any = None,
) -> WebIngestResult:
    """Prepare then store a verified lead's bytes (no savepoint of its own)."""
    prepared = await prepare_verified_lead_document(
        content=content,
        url=url,
        fetched_url=fetched_url,
        truncated=truncated,
        cfg=cfg,
        company_id=company_id,
        headers=headers,
        research_job_id=research_job_id,
        web_search_result_id=web_search_result_id,
        provider=provider,
        candidates=candidates,
        pool=pool,
        store=store,
        resolver=resolver,
        runtime=runtime,
    )
    if isinstance(prepared, WebIngestResult):
        return prepared
    return await store_verified_lead_document(
        session, prepared, cfg=cfg, store=store, backend=backend
    )


async def version_for_external_evidence(session: Any, evidence_id: str) -> Any:
    """The corpus version an ``ev:x:`` id resolves to (spec §12.2), or None.

    None is a real answer: verification can succeed while ingestion was refused
    (TDM, wall, flag off). A consumer must treat an unresolved id as "verified, not
    stored", never as missing evidence.
    """
    if not (evidence_id or "").startswith("ev:x:"):
        return None
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentVersion
    from app.models.research_lead import ResearchLeadRecord

    return (
        await session.execute(
            select(ResearchDocumentVersion)
            .join(
                ResearchLeadRecord,
                ResearchLeadRecord.research_document_version_id == ResearchDocumentVersion.id,
            )
            .where(ResearchLeadRecord.promoted_evidence_id == evidence_id)
            .limit(1)
        )
    ).scalar_one_or_none()


__all__ = [
    "REASON_DISABLED",
    "REASON_NO_SUBJECT",
    "REASON_TDM_RESERVED",
    "REASON_TRUNCATED",
    "STATE_DISABLED",
    "STATE_INGESTED",
    "STATE_NOT_INGESTED",
    "STATE_REUSED",
    "SUBJECT_SCOPES",
    "PreparedLeadDocument",
    "PreparedWebDocument",
    "WebIngestResult",
    "ingest_enabled",
    "ingest_verified_lead_document",
    "ingest_web_document",
    "linked_version",
    "prepare_verified_lead_document",
    "prepare_web_document",
    "store_verified_lead_document",
    "store_web_document",
    "transport_for",
    "version_for_external_evidence",
    "write_subjects",
]
