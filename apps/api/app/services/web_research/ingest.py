"""Web documents into the Research Corpus — open-web W3 (spec §12.3).

THE ONE WRITE PATH
==================
Every fetched web document follows the same steps, whether it came from a search
result, a crawl, a user URL or a verified lead (``fetch_public_source``):

1. a ``web_fetch_attempts`` row (written by ``open_web_fetch``, or here for a lead);
2. refusals first — no bytes, a TDM reservation, a truncated body, an unsupported type
   are never ingested;
3. extraction in the killable process pool (``extract.py``);
4. deterministic classification: source class → tier + access class, ``use_constraint``,
   document kind, injection taint (``classify.py``);
5. duplicate check by content hash — the same bytes already stored are LINKED (a
   subjects row), never re-chunked; SimHash + a provisional ``origin_key`` are stored
   for W4;
6. raw bytes → the artifact store under the version's access class, with the web TTL
   (``V3_WEB_ARTIFACT_RETENTION_DAYS``) when the constraint is ``unknown``;
7. ``ExtractedDocument`` — SHARED by content hash, so its ``company_id`` is whoever
   extracted it first and is never read as this run's company;
8. ``ingest_extracted_document`` → a version with ``access_class``,
   ``transport="open_web:<provider|direct|user>"``, ``content_origin``, tier and the
   W3 fields, then derivation, pages, sections, tables and chunks (``ev:c:`` ids);
9. ``research_document_subjects`` rows from entity mention detection;
10. indexing, so the chunks are retrievable.

FLAG
====
``V3_WEB_CORPUS_INGEST_ENABLED`` (default off) — this module is its only consumer — and
``V3_CORPUS_ENABLED``. Off: ``state="disabled"``, no extraction, no query, no row.

Logs carry codes, counts and a 12-character URL hash — never a URL, never page text.
"""

from __future__ import annotations

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

_TRANSPORT_MAX = 100


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
    notes: list[str] = field(default_factory=list)

    @property
    def stored(self) -> bool:
        return self.state in (STATE_INGESTED, STATE_REUSED) and self.version_id is not None


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
    """Put one fetched document (an ``OpenWebFetchResult``) into the corpus. Never raises
    for a document-level outcome; a database error propagates to the caller's savepoint.

    ``company_id`` makes it a company document (``subject_scope="company"``). Without
    one, ``subject_scope`` (``theme | industry | macro``) is required; the document is
    company-less and deduplicated by the partial unique index on its key.
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

    from app.services.web_research.classify import (
        classify_document_kind,
        classify_source,
        injection_assessment,
    )
    from app.services.web_research.dedup import (
        origin_key_for,
        simhash64,
        to_signed64,
        version_with_hash,
    )
    from app.services.web_research.extract import (
        WEB_EXTRACTOR_VERSION,
        extract_web_document,
    )

    url = fetched.final_url or fetched.requested_url
    canonical = fetched.canonical_url or url
    host = _host(url)
    stamp = now or datetime.now(timezone.utc)
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
    taint = injection_assessment(
        extraction.main_text,
        hidden_text=extraction.hidden_text,
        metadata_text=" ".join(
            part for part in (meta.title, meta.description, extraction.document_info_text) if part
        ),
    )
    result = WebIngestResult(
        state=STATE_NOT_INGESTED,
        source_class=classification.source_class,
        tier=classification.tier,
        access_class=classification.access_class,
        use_constraint=classification.use_constraint,
        document_kind=kind,
        injection_suspect=taint.suspect,
        injection_signals=taint.signals,
        extraction_method=extraction.method,
        extraction_confidence=extraction.confidence,
        stopped_by=extraction.stopped_by,
        published_at_source=meta.published_at_source,
    )
    if not extraction.extracted:
        result.reason = extraction.failure_code or REASON_EXTRACTION
        _log(result, url)
        return result


    # Same bytes already in the corpus: LINK, never re-chunk (spec §14.1).
    existing = await version_with_hash(session, content_hash)
    mentions = _mentions(extraction.main_text, candidates, host)
    if existing is not None:
        result.state = STATE_REUSED
        result.version_id = existing.id
        result.document_id = existing.research_document_id
        result.extracted_document_id = existing.extracted_document_id
        if existing.web_fetch_attempt_id is None and getattr(fetched, "attempt_id", None):
            existing.web_fetch_attempt_id = fetched.attempt_id
        result.subjects_written = await write_subjects(
            session,
            document_id=existing.research_document_id,
            version_id=existing.id,
            company_id=company_id,
            mentions=mentions,
        )
        result.notes.append("same bytes already stored; linked, not re-chunked")
        _log(result, url)
        return result

    from app.services.corpus.artifacts.service import record_artifact, store_raw_artifact
    from app.services.corpus.documents import (
        CorpusIngestResult,
        WebVersionFields,
        ingest_extracted_document,
    )
    from app.services.corpus.policy import default_policy_for
    from app.services.sources.disclosure_period_policy import PERIOD_POLICY_TITLE_ONLY

    policy = default_policy_for(
        classification.access_class,
        retention_days=_retention_days(cfg, classification.use_constraint),
        now=stamp,
    )
    media_type = _MEDIA_TYPES.get(
        fetched.content_class, fetched.mime_sniffed or "application/octet-stream"
    )
    stored = await store_raw_artifact(
        fetched.content,
        media_type=media_type,
        access_class=classification.access_class,
        cfg=cfg,
        store=store,
        policy=policy,
        now=stamp,
    )
    await record_artifact(session, stored, cfg=cfg, now=stamp)

    body = extraction.extraction
    title = (meta.title or "")[:500] or None
    transport = transport_for(provider)
    document = await _get_or_create_extracted_document(
        session,
        content_hash=content_hash,
        canonical_url=canonical,
        transport=transport,
        tier=classification.tier,
        mime_type=media_type,
        title=title,
        published_at=meta.published_at,
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
        content_hash=content_hash,
        canonical_url=canonical,
        provider=transport,
        source_tier=classification.tier,
        company_id=company_id,
        source_type=SOURCE_TYPE_OPEN_WEB,
        title=title,
        mime_type=media_type,
        doc_date=meta.published_at,
        retrieved_at=stamp,
        status=body.status,
    )
    artifact = SimpleNamespace(
        raw_artifact=stored,
        extraction=body,
        title=title,
        source_url=canonical,
        doc_kind=kind,
        period_policy=PERIOD_POLICY_TITLE_ONLY,
        failure_code=None,
    )
    origin = origin_key_for(host)
    web = WebVersionFields(
        web_fetch_attempt_id=getattr(fetched, "attempt_id", None),
        use_constraint=classification.use_constraint,
        injection_suspect=taint.suspect,
        simhash=to_signed64(simhash64(extraction.main_text)),
        origin_key=origin,
        published_at_source=meta.published_at_source if meta.published_at else None,
        source_class=classification.source_class,
        web_extractor_version=WEB_EXTRACTOR_VERSION,
        subject_scope=scope,
        theme_key=(theme_key or None) if company_id is None else None,
        content_origin=origin,
    )
    counts = CorpusIngestResult()
    version = await ingest_extracted_document(
        session, artifact=artifact, document=view,  # type: ignore[arg-type]
        cfg=cfg, now=stamp, result=counts, web=web,
    )
    if version is None:
        result.reason = "corpus_version_not_written"
        _log(result, url)
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
    )
    result.indexed = await _index(session, version.id, cfg=cfg, backend=backend)
    _log(result, url)
    return result


_MEDIA_TYPES = {"pdf": "application/pdf", "html": "text/html", "text": "text/plain"}


def _mentions(text: str, candidates: Any, host: str) -> list[Any]:
    if not candidates:
        return []
    from app.services.web_research.entities import detect_mentions

    return detect_mentions(text, list(candidates), page_host=host or None)


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


async def write_subjects(
    session: Any,
    *,
    document_id: uuid.UUID,
    version_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    mentions: list[Any],
) -> int:
    """``research_document_subjects`` rows for one document. Idempotent.

    The run's own company is the ``primary`` subject (with the confidence the text
    earned, or none when the text never names it). Every other mention is
    ``mentioned``. A brand mention carries its ``segment:`` / ``brand:`` scope — never
    ``group``. A ``name_only`` match is recorded for a LEAD only.
    """
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentSubject
    from app.services.web_research.entities import CONF_NAME_ONLY

    existing_keys = {
        (row.company_id, row.legal_entity_id, row.relation, row.scope_key)
        for row in (
            await session.execute(
                select(ResearchDocumentSubject).where(
                    ResearchDocumentSubject.research_document_id == document_id
                )
            )
        ).scalars()
    }
    primary_mention = next(
        (
            m
            for m in mentions
            if company_id is not None and m.company_id == company_id and m.scope_key is None
        ),
        None,
    )
    planned: list[tuple[Any, Any, str, str | None, str, str | None, str | None]] = []
    if company_id is not None:
        planned.append(
            (
                company_id,
                getattr(primary_mention, "legal_entity_id", None),
                "primary",
                getattr(primary_mention, "confidence", None),
                getattr(primary_mention, "method", None) or "research_run",
                None,
                getattr(primary_mention, "surface", None),
            )
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
            (
                mention.company_id,
                mention.legal_entity_id,
                "mentioned",
                mention.confidence,
                mention.method,
                mention.scope_key,
                mention.surface,
            )
        )
    chunks = await _chunk_texts(session, version_id) if planned else []
    written = 0
    for company, entity, relation, confidence, method, scope_key, surface in planned:
        key = (company, entity, relation, scope_key)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        session.add(
            ResearchDocumentSubject(
                id=uuid.uuid4(),
                research_document_id=document_id,
                company_id=company,
                legal_entity_id=entity,
                relation=relation,
                confidence=confidence,
                method=(method or "research_run")[:40],
                scope_key=scope_key,
                evidence_chunk_id=_chunk_for(chunks, surface),
            )
        )
        written += 1
    if written:
        await session.flush()
    return written


async def _chunk_texts(session: Any, version_id: uuid.UUID | None) -> list[tuple[str, str]]:
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


def _chunk_for(chunks: list[tuple[str, str]], surface: str | None) -> str | None:
    if not surface or not chunks:
        return None
    from app.services.web_research.entities import fold

    needle = fold(surface)
    for chunk_id, text in chunks:
        if needle and needle in fold(text):
            return chunk_id[:120]
    return None


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


async def ingest_verified_lead_document(
    session: Any,
    *,
    content: bytes | None,
    url: str,
    fetched_url: str | None,
    truncated: bool,
    cfg: Any,
    company_id: uuid.UUID | None,
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
    """Ingest the bytes ``verify_lead`` fetched and VERIFIED, through the same path.

    ``verify_lead`` fetches through ``safe_fetch_document``, which predates robots.txt
    and TDM handling, so before anything is stored this path asks the open-web policy
    for the origin's robots/TDMRep decision (``fetch.ingestion_clearance``) and reads
    the page's own TDM meta signals. Any reservation → not ingested. A
    ``web_fetch_attempts`` row (origin ``lead``) records the retrieval, so the version
    has the same provenance as every other web document.
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
    clearance = await ingestion_clearance(
        final,
        session=session,
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
        html = content_mod.decode_text(content, charset)
        if robots.tdm_signals_from_html(html):
            return WebIngestResult(STATE_NOT_INGESTED, REASON_TDM_RESERVED)
        js_required = content_mod.js_required(len(content), html)

    content_hash = hashlib.sha256(content).hexdigest()
    stored_final = stored_url(final) or final
    canonical = canonical_url(final) or stored_final
    attempt_id = uuid.uuid4()
    session.add(
        WebFetchAttempt(
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
    )
    await session.flush()
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
    return await ingest_web_document(
        session,
        fetched,
        cfg=cfg,
        provider=provider or "lead",
        company_id=company_id,
        candidates=candidates,
        pool=pool,
        store=store,
        backend=backend,
    )


async def version_for_external_evidence(session: Any, evidence_id: str) -> Any:
    """The corpus version an ``ev:x:`` id resolves to (spec §12.2), or None."""
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
    "WebIngestResult",
    "ingest_enabled",
    "ingest_verified_lead_document",
    "ingest_web_document",
    "transport_for",
    "version_for_external_evidence",
    "write_subjects",
]
