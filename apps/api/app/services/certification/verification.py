"""Certificate verification states and the qualification summary — V3.21 slice 21.5.

Spec §6.2–6.3, ADR-112. For every certificate the real-asset block found
(``electricity.real_asset.certifications_in``) this module decides ONE state:

* ``third_party_corroborated``: the same certificate (body + standard, naming the
  company) appears on the body's own host, or in a T1–T3 document from another party;
* ``issuer_claim``: stated only in the company's own documents;
* ``body_unrecognised``: the named body is neither an STL member nor on the curated list;
* ``unverified``: anything else (no body named, unresolvable evidence, nothing found).

WHERE CORROBORATION MAY COME FROM
=================================
1. The finding's own evidence, when that chunk is from another party's T1–T3 document or
   from the body's host.
2. Other HELD documents (``research_document_chunks``): a bounded text match on body
   alias + standard + the company's name, re-checked in Python with word boundaries.
3. Only when web search is on: ONE ``include_domains`` search restricted to the body's
   host(s) per (body, standard), at most ``MAX_WEB_SEARCHES_PER_RUN`` per run, and one
   fetch of the top on-host result to read it. A search snippet is never evidence.

No certificate database is scraped (none is public in bulk). "Not corroborated" is never
read as "false": it is ``issuer_claim`` or ``unverified``, and the summary says what could
not be checked.

THE PRODUCT IS NOT MATCHED. A document that names the body, the standard and the
company is taken as the same certificate. That is stated in the block's note.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.certification.bodies import (
    CertificationBody,
    build_registry,
    current_stl_members,
    ensure_stl_members_fresh,
    match_body,
    named_body_text,
    names_body,
    on_hosts,
)

VERSION = "v3.21.5"

STATE_ISSUER_CLAIM = "issuer_claim"
STATE_CORROBORATED = "third_party_corroborated"
STATE_BODY_UNRECOGNISED = "body_unrecognised"
STATE_UNVERIFIED = "unverified"
STATES: tuple[str, ...] = (
    STATE_CORROBORATED, STATE_ISSUER_CLAIM, STATE_BODY_UNRECOGNISED, STATE_UNVERIFIED)

MAX_WEB_SEARCHES_PER_RUN = 5
MAX_CANDIDATE_CHUNKS = 25

DOC_BODY_HOST = "body_host"
DOC_THIRD_PARTY = "third_party"
DOC_ISSUER = "issuer"
DOC_INELIGIBLE = "ineligible"

WEB_DISABLED = "disabled"
WEB_NOT_NEEDED = "not_needed"
WEB_RAN = "ran"

#: Source classes that are the company speaking (``web_research.classify``).
_ISSUER_CLASSES = frozenset({
    "issuer_filing", "exchange_announcement", "company_press_release",
    "investor_presentation", "company_web_page",
})
_THIRD_PARTY_TIERS = ("T2_", "T3_")

_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|ag|se|sa|"
    r"s\.a|n\.v|nv|b\.v|asa|ab|oyj|spa|s\.p\.a|gmbh|holdings?|group)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HeldDocument:
    """One held chunk and the version it came from — what a state is decided on."""

    evidence_id: str | None
    text: str | None
    company_id: str | None
    source_tier: str | None
    source_class: str | None
    canonical_url: str | None
    published_at: str | None = None
    retrieved_at: str | None = None


@dataclass(frozen=True)
class Subject:
    company_id: str | None
    names: tuple[str, ...]
    hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebCheck:
    """The outcome of one body-host search. ``url`` only when the page was read."""

    searched: bool
    corroborated: bool = False
    url: str | None = None
    retrieved_at: str | None = None
    note: str | None = None


WebChecker = Callable[[CertificationBody, Mapping[str, Any], Subject], Awaitable[WebCheck]]


# ── Pure core ─────────────────────────────────────────────────────────────── #


def subject_names(name: str | None, ticker: str | None = None) -> tuple[str, ...]:
    """The company's name with and without its legal suffix, and a ticker of ≥ 3 letters."""
    out: list[str] = []
    full = " ".join((name or "").split())
    if full:
        out.append(full)
        short = full
        for _ in range(3):
            stripped = _LEGAL_SUFFIX_RE.sub("", short).strip(" ,")
            if stripped == short:
                break
            short = stripped
        if short and short != full and len(short) >= 3:
            out.append(short)
    if ticker and len(ticker) >= 3 and ticker.isalnum():
        out.append(ticker.upper())
    return tuple(dict.fromkeys(out))


def _standard_key(standard: str | None) -> str | None:
    return re.sub(r"\s+", "", standard).upper() if standard else None


def names_standard(text: str, standard: str | None) -> bool:
    if not standard:
        return True
    key = _standard_key(standard) or ""
    squeezed = re.sub(r"\s+", "", text or "").upper()
    return key in squeezed


def names_subject(text: str, subject: Subject) -> bool:
    for name in subject.names:
        flags = 0 if name.isupper() else re.IGNORECASE
        if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text or "", flags):
            return True
    return False


def classify_document(
    doc: HeldDocument, *, subject: Subject, body: CertificationBody,
) -> str:
    """Who is speaking in ``doc``: the body's host, another party, the issuer, or neither."""
    if body.hosts and on_hosts(doc.canonical_url, body.hosts):
        return DOC_BODY_HOST
    if subject.hosts and on_hosts(doc.canonical_url, subject.hosts):
        return DOC_ISSUER
    klass = doc.source_class or ""
    tier = doc.source_tier or ""
    same_company = doc.company_id is None or (
        subject.company_id is not None and doc.company_id == subject.company_id)
    if klass in _ISSUER_CLASSES:
        return DOC_ISSUER
    if tier.startswith("T1"):
        return DOC_ISSUER if same_company else DOC_THIRD_PARTY
    if tier.startswith(_THIRD_PARTY_TIERS):
        return DOC_THIRD_PARTY
    return DOC_INELIGIBLE


def corroborates(
    doc: HeldDocument, item: Mapping[str, Any], *, subject: Subject, body: CertificationBody,
) -> bool:
    """``doc`` is another party's (or the body's) and names body + standard + company."""
    if classify_document(doc, subject=subject, body=body) not in (DOC_BODY_HOST,
                                                                  DOC_THIRD_PARTY):
        return False
    text = doc.text or ""
    about_subject = names_subject(text, subject) or (
        doc.company_id is not None and doc.company_id == subject.company_id)
    return (names_body(text, body) and names_standard(text, item.get("standard"))
            and about_subject)


def _source_view(doc: HeldDocument, kind: str) -> dict[str, Any]:
    return {"kind": kind, "evidence_id": doc.evidence_id, "source_url": doc.canonical_url,
            "source_tier": doc.source_tier, "source_class": doc.source_class,
            "published_at": doc.published_at, "retrieved_at": doc.retrieved_at}


def resolve_body(
    item: Mapping[str, Any], registry: Sequence[CertificationBody],
) -> tuple[CertificationBody | None, str | None]:
    """The recognised body the certificate names, and the name as written."""
    written = str(item.get("body") or "").strip() or None
    quote = str(item.get("quote") or "")
    body = match_body(quote, registry)
    if body is not None and written and not any(
            written.upper() in a.upper() for a in body.aliases):
        # The quote names another body first; the extracted name decides.
        body = match_body(written, registry)
    elif body is None and written:
        body = match_body(written, registry)
    if body is not None:
        return body, written or body.name
    return None, written or named_body_text(quote)


def decide(
    item: Mapping[str, Any],
    *,
    body: CertificationBody | None,
    body_written: str | None,
    subject: Subject,
    evidence_docs: Sequence[HeldDocument],
    candidate_docs: Sequence[HeldDocument],
    web: WebCheck | None,
) -> dict[str, Any]:
    """The verification record for one certificate. Pure."""
    record: dict[str, Any] = {
        "state": STATE_UNVERIFIED,
        "body_named": body_written,
        "body": body.view() if body else None,
        "basis": None,
        "corroboration": [],
        "checked": {
            "held_documents": body is not None,
            "web_search": "searched" if web and web.searched else "not_run",
        },
    }
    if body is None:
        if body_written:
            record["state"] = STATE_BODY_UNRECOGNISED
            record["basis"] = "The named body is neither an STL member nor on the curated list."
        else:
            record["basis"] = "No testing or certification body is named."
        return record
    own = [d for d in evidence_docs if corroborates(d, item, subject=subject, body=body)]
    others = [d for d in candidate_docs if corroborates(d, item, subject=subject, body=body)]
    sources = [_source_view(d, "finding_evidence") for d in own] + [
        _source_view(d, "held_document") for d in others[:3]]
    if web is not None and web.corroborated and web.url:
        sources.append({"kind": "body_host_page", "evidence_id": None, "source_url": web.url,
                        "source_tier": None, "source_class": None, "published_at": None,
                        "retrieved_at": web.retrieved_at})
    if sources:
        record["state"] = STATE_CORROBORATED
        record["corroboration"] = sources
        record["basis"] = ("Named with the same body and standard in a document not "
                           "published by the company.")
        return record
    kinds = [classify_document(d, subject=subject, body=body) for d in evidence_docs]
    if kinds and all(k == DOC_ISSUER for k in kinds):
        record["state"] = STATE_ISSUER_CLAIM
        record["basis"] = "Stated only in the company's own documents."
    elif not kinds:
        record["basis"] = "The finding's evidence could not be resolved to a held document."
    else:
        record["basis"] = ("Not found in a held document from another party or on the "
                           "body's host.")
    return record


def qualification_evidence_block(
    items: Sequence[Mapping[str, Any]],
    *,
    stl_register: Mapping[str, Any],
    web_search: Mapping[str, Any],
) -> dict[str, Any]:
    """The ``competitive_position.qualification_evidence`` block from verified items."""
    certificates: list[dict[str, Any]] = []
    for it in items:
        v = it.get("verification") or {}
        body = v.get("body") or {}
        certificates.append({
            "certification": it.get("certification"),
            "standard": it.get("standard"),
            "body": body.get("name") or v.get("body_named"),
            "body_key": body.get("key"),
            "body_country": body.get("country"),
            "stl_member": bool(body.get("stl_member")),
            "date": it.get("date"),
            "date_state": it.get("date_state"),
            "state": v.get("state") or STATE_UNVERIFIED,
            "finding_id": it.get("finding_id"),
            "evidence_ids": list(it.get("evidence_ids") or []),
            "corroborating_urls": [c.get("source_url") for c in v.get("corroboration") or []
                                   if c.get("source_url")][:3],
        })
    certificates.sort(key=lambda c: (str(c["body"] or "~"), str(c["date"] or "")))
    by_body: dict[str, dict[str, Any]] = {}
    for c in certificates:
        name = str(c["body"] or "No body named")
        row = by_body.setdefault(name, {"body": c["body"], "body_key": c["body_key"],
                                        "stl_member": c["stl_member"], "count": 0,
                                        "dates": [], "states": {}})
        row["count"] += 1
        if c["date"]:
            row["dates"].append(c["date"])
        row["states"][c["state"]] = row["states"].get(c["state"], 0) + 1
    counts = {s: sum(1 for c in certificates if c["state"] == s) for s in STATES}
    total = len(certificates)
    stl_named = sorted({str(c["body"]) for c in certificates if c["stl_member"]})
    not_verifiable: list[str] = []
    if counts[STATE_ISSUER_CLAIM]:
        not_verifiable.append(
            f"{counts[STATE_ISSUER_CLAIM]} certificate(s) are stated only by the company; "
            "no other party's document or the body's host was found naming them.")
    if counts[STATE_BODY_UNRECOGNISED]:
        not_verifiable.append(
            f"{counts[STATE_BODY_UNRECOGNISED]} certificate(s) name a body that is neither "
            "an STL member nor on the curated list.")
    if counts[STATE_UNVERIFIED]:
        not_verifiable.append(
            f"{counts[STATE_UNVERIFIED]} certificate(s) could not be checked (no body named, "
            "or the evidence could not be resolved).")
    if web_search.get("state") == WEB_DISABLED and total:
        not_verifiable.append(
            "Web search is off, so the bodies' own sites were not searched; only held "
            "documents were checked.")
    not_verifiable.append(
        "Certificate databases are not public in bulk and are not read; a certificate "
        "that is not corroborated here is not shown to be false.")
    return {
        "version": VERSION,
        "state": "certificates_found" if total else "none_found",
        "total": total,
        "third_party_corroborated": counts[STATE_CORROBORATED],
        "share_third_party_corroborated": (
            round(counts[STATE_CORROBORATED] / total, 4) if total else None),
        "states": counts,
        "certificates": certificates,
        "by_body": list(by_body.values()),
        "any_stl_member": bool(stl_named),
        "stl_members_named": stl_named,
        "stl_register": dict(stl_register),
        "web_search": dict(web_search),
        "not_verifiable": not_verifiable,
        "note": (
            "Certificates are read from this run's cited findings. A certificate is "
            "third-party corroborated when a document not published by the company, or a "
            "page on the body's own site, names the same body, standard and company; the "
            "product itself is not matched. STL membership is read from the STL members "
            "page."
        ),
    }


# ── Database reads ────────────────────────────────────────────────────────── #


def _row_doc(row: Any) -> HeldDocument:
    return HeldDocument(
        evidence_id="ev:" + str(row[0]) if not str(row[0]).startswith("ev:") else str(row[0]),
        text=row[1], company_id=str(row[2]) if row[2] else None, source_tier=row[3],
        source_class=row[4], canonical_url=row[5],
        published_at=row[6].isoformat() if row[6] else None,
        retrieved_at=row[7].isoformat() if row[7] else None,
    )


def _columns() -> tuple[Any, ...]:
    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V

    return (C.chunk_id, C.text, C.company_id, C.source_tier, V.source_class,
            V.canonical_url, C.published_at, V.retrieved_at)


async def load_evidence_documents(
    session: Any, evidence_ids: Sequence[str],
) -> dict[str, HeldDocument]:
    """The held chunks behind the given evidence ids (``ev:<chunk_id>``)."""
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V
    from app.services.corpus.retrieval import chunk_id_from_evidence_id

    ids = [chunk_id_from_evidence_id(str(e)) for e in dict.fromkeys(evidence_ids)
           if str(e).startswith("ev:") and not str(e).startswith("ev:x:")]
    if not ids:
        return {}
    rows = (await session.execute(
        select(*_columns()).join(V, V.id == C.research_document_version_id)
        .where(C.chunk_id.in_(ids))
    )).all()
    return {d.evidence_id or "": d for d in (_row_doc(r) for r in rows)}


async def find_candidate_documents(
    session: Any, body: CertificationBody, item: Mapping[str, Any], subject: Subject,
) -> list[HeldDocument]:
    """Held chunks that may name the same certificate. Bounded; re-checked in Python."""
    from sqlalchemy import or_, select

    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V

    alias_terms = [C.text.ilike(f"%{a}%") for a in body.aliases if len(a) >= 3]
    if not alias_terms:
        return []
    clauses: list[Any] = [or_(*alias_terms)]
    standard = item.get("standard")
    if standard:
        digits = re.search(r"\d[\d.\-]*", str(standard))
        if digits:
            clauses.append(C.text.ilike(f"%{digits.group(0)}%"))
    about: list[Any] = [C.text.ilike(f"%{n}%") for n in subject.names]
    if subject.company_id:
        about.append(C.company_id == uuid.UUID(subject.company_id))
    if not about:
        return []
    clauses.append(or_(*about))
    rows = (await session.execute(
        select(*_columns()).join(V, V.id == C.research_document_version_id)
        .where(*clauses).limit(MAX_CANDIDATE_CHUNKS)
    )).all()
    return [_row_doc(r) for r in rows]


# ── Web: one include_domains search on the body's host ────────────────────── #


def default_web_checker(session: Any, cfg: Any) -> WebChecker:
    """The live checker: one restricted search, one fetch of the top on-host result."""

    async def check(body: CertificationBody, item: Mapping[str, Any],
                    subject: Subject) -> WebCheck:
        from app.services.providers.contracts import QueryFamily, SearchRequest
        from app.services.web_research.queries import sanitise_query
        from app.services.web_research.search import SearchContext, run_searches
        from app.services.web_research.stage import persist_factory_for

        name = subject.names[1] if len(subject.names) > 1 else (subject.names or ("",))[0]
        what = item.get("standard") or item.get("certification") or ""
        sanitised = sanitise_query(f"{name} {body.name} {what}")
        if not sanitised.ok:
            return WebCheck(searched=False, note="query_refused")
        request = SearchRequest(
            query=sanitised.text, family=QueryFamily.COMPETITIVE, max_results=3,
            include_domains=body.hosts, origin="certification", template_version=VERSION)
        run = await run_searches(
            session, [request],
            SearchContext(company_id=uuid.UUID(subject.company_id)
                          if subject.company_id else None, stage="certification"),
            cfg=cfg, persist_session_factory=persist_factory_for(session))
        outcome = run.outcomes[0] if run.outcomes else None
        if outcome is None or not outcome.execution.executed:
            return WebCheck(searched=bool(outcome), note=run.state)
        hit = next((r for r in outcome.results if on_hosts(r.url, body.hosts)), None)
        if hit is None:
            return WebCheck(searched=True, note="no_on_host_result")
        text = await _read_page(hit.url, body.hosts)
        if text is None:
            return WebCheck(searched=True, note="page_not_read")
        doc = HeldDocument(None, text, None, None, None, hit.url)
        if corroborates(doc, item, subject=subject, body=body):
            return WebCheck(searched=True, corroborated=True, url=hit.url,
                            retrieved_at=datetime.now(timezone.utc).isoformat())
        return WebCheck(searched=True, note="not_named_on_page")

    return check


async def _read_page(url: str, hosts: tuple[str, ...]) -> str | None:
    from app.services.certification.bodies import decode_page
    from app.services.sources.document_fetcher import safe_fetch_document
    from app.services.web_research.content import visible_text

    result = await safe_fetch_document(url, allowed_domains=hosts, resolve_ip=True,
                                       max_bytes=2 * 1024 * 1024,
                                       total_deadline_seconds=20.0)
    if result.error or result.blocked or not result.content:
        return None
    if result.document_type not in ("html", "text"):
        return None
    page = decode_page(result.content)
    return visible_text(page) if result.document_type == "html" else page


# ── The step ──────────────────────────────────────────────────────────────── #


def _subject(company: Any) -> Subject:
    from urllib.parse import urlsplit

    website = str(getattr(company, "website", None) or "")
    host = ""
    if website:
        try:
            host = (urlsplit(website if "//" in website else "https://" + website)
                    .hostname or "").lower()
        except ValueError:
            host = ""
    if host.startswith("www."):
        host = host[4:]
    cid = getattr(company, "id", None)
    return Subject(
        company_id=str(cid) if cid else None,
        names=subject_names(getattr(company, "name", None), getattr(company, "ticker", None)),
        hosts=(host,) if host else (),
    )


async def certification_step(
    session: Any,
    company: Any,
    real_asset: Mapping[str, Any] | None,
    *,
    cfg: Any,
    web_checker: WebChecker | None = None,
    refresh: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(real_asset with a verification on each certificate, qualification_evidence)``.

    Flag off, or no real-asset block: ``(real_asset unchanged, None)`` — the report is
    byte-identical. Never raises: on an error the inputs come back unchanged.
    """
    if real_asset is None or not getattr(cfg, "v3_certification_bodies_enabled", False):
        return (dict(real_asset) if real_asset is not None else None), None
    try:
        return await _verify(session, company, real_asset, cfg=cfg,
                             web_checker=web_checker, refresh=refresh)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        return dict(real_asset), None


async def _verify(
    session: Any, company: Any, real_asset: Mapping[str, Any], *, cfg: Any,
    web_checker: WebChecker | None, refresh: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if refresh:
        try:
            await ensure_stl_members_fresh()
        except Exception:  # noqa: BLE001 - the last good list or the snapshot stands
            pass
    members = current_stl_members()
    registry = build_registry(members)
    subject = _subject(company)
    certs = dict(real_asset.get("certifications") or {})
    items = [dict(i) for i in certs.get("items") or []]
    web_on = bool(getattr(cfg, "v3_web_search_enabled", False))
    # The body-host search runs only when web search is on — an injected checker too.
    checker = (web_checker or default_web_checker(session, cfg)) if web_on else None
    searches = 0
    web_cache: dict[tuple[str, str | None], WebCheck] = {}
    for item in items:
        body, written = resolve_body(item, registry)
        evidence: list[HeldDocument] = []
        candidates: list[HeldDocument] = []
        if body is not None:
            try:
                async with session.begin_nested():
                    found = await load_evidence_documents(
                        session, [str(e) for e in item.get("evidence_ids") or []])
                    evidence = list(found.values())
                    candidates = await find_candidate_documents(session, body, item, subject)
            except Exception:  # noqa: BLE001 - unread documents mean "unverified"
                evidence, candidates = [], []
        web: WebCheck | None = None
        pre = decide(item, body=body, body_written=written, subject=subject,
                     evidence_docs=evidence, candidate_docs=candidates, web=None)
        if (body is not None and body.hosts and checker is not None
                and pre["state"] != STATE_CORROBORATED):
            key = (body.key, _standard_key(item.get("standard")))
            if key in web_cache:
                web = web_cache[key]
            elif searches < MAX_WEB_SEARCHES_PER_RUN:
                searches += 1
                try:
                    web = await checker(body, item, subject)
                except Exception:  # noqa: BLE001 - a failed search is "not searched"
                    web = WebCheck(searched=False, note="error")
                web_cache[key] = web
        item["verification"] = (
            pre if web is None else
            decide(item, body=body, body_written=written, subject=subject,
                   evidence_docs=evidence, candidate_docs=candidates, web=web))
    certs["items"] = items
    annotated = {**dict(real_asset), "certifications": certs}
    web_state = {
        "state": WEB_DISABLED if not web_on else (WEB_RAN if searches else WEB_NOT_NEEDED),
        "searches": searches,
        "max_searches": MAX_WEB_SEARCHES_PER_RUN,
    }
    block = qualification_evidence_block(
        items, stl_register=members.summary(), web_search=web_state)
    return annotated, block


__all__ = [
    "MAX_WEB_SEARCHES_PER_RUN",
    "STATES",
    "STATE_BODY_UNRECOGNISED",
    "STATE_CORROBORATED",
    "STATE_ISSUER_CLAIM",
    "STATE_UNVERIFIED",
    "HeldDocument",
    "Subject",
    "WebCheck",
    "certification_step",
    "classify_document",
    "corroborates",
    "decide",
    "qualification_evidence_block",
    "resolve_body",
    "subject_names",
]
