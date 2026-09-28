"""UK regulated disclosures from the FCA National Storage Mechanism.

IDENTITY — BY LEI, NEVER BY NAME
================================
Measured: a keyword NSM search for "Pensana" returned an Alkemy Capital filing. So an
issuer is matched only through identifiers:

1. the LSE's own instrument record for the listing (V3.19 directories) gives the ISIN
   and the exchange's issuer name;
2. the issuer's LEI — from the platform's own entity identifiers when held, otherwise
   from GLEIF (the LEI authority) for that ISIN, accepted only when exactly one record
   answers, the LEI is well-formed, and GLEIF's legal name agrees with the exchange's;
3. the NSM query is by that LEI, and every returned record must carry it — any other
   record is refused and counted.

CONTENT
=======
The document a record points at — the RNS text (HTML), an annual-report PDF, or a tagged
XHTML report — at ``https://data.fca.org.uk/artefacts/<link>``. The link is accepted
only when it is a plain ``NSM/…`` path whose segments include the record's own id; the
URL is built here against a fixed host, never taken whole from the response. ZIP
packages are never fetched.

TERMS: see ``docs/non-us-primary-documents.md`` — FCA consent is required before any
public or commercial use.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.corpus.filing_evidence import (
    canonical_document_ref,
    url_has_document_segment,
)
from app.services.sources.disclosures.model import (
    REASON_DATA_NOT_SOURCED,
    REASON_IDENTITY_UNVERIFIED,
    REASON_SOURCE_UNAVAILABLE,
    SOURCE_UK_FCA_NSM,
    VENUE_LSE,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.disclosures.relevance import classify_uk

NSM_SEARCH_URL = "https://api.data.fca.org.uk/search?index=nsm-search"
NSM_SEARCH_HOST = "api.data.fca.org.uk"
NSM_ARTEFACT_BASE = "https://data.fca.org.uk/artefacts/"
NSM_ARTEFACT_HOST = "data.fca.org.uk"
GLEIF_ISIN_URL = "https://api.gleif.org/api/v1/lei-records?filter%5Bisin%5D={isin}"
GLEIF_HOST = "api.gleif.org"

#: Records read per listing. The NSM returns newest first; 100 covers well over a year
#: of an active small cap's disclosures, and the listing is one bounded request.
NSM_PAGE_SIZE = 100

_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
#: A plain NSM artefact path: no scheme, no host, no traversal, a document extension.
_NSM_LINK_RE = re.compile(r"^NSM/[A-Za-z0-9][A-Za-z0-9/_.-]{0,300}\.(?:pdf|html|htm|xhtml)$")


def _parse_time(value: Any) -> datetime | None:
    try:
        text = str(value or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


#: A portal submission's amendment suffix: ``NI-000131364-0`` is version 0 of the document
#: filed at ``NSM/Portal/NI-000131364/…``. The base id is the DOCUMENT; the suffix is
#: its amendment, and only the latest amendment is read (``latest_flag``).
_AMENDMENT_SUFFIX_RE = re.compile(r"^(NI-\d{6,12})-\d{1,3}$")


def base_document_ref(ref: str) -> str:
    """The document's own id, without an NSM amendment suffix."""
    match = _AMENDMENT_SUFFIX_RE.match(ref or "")
    return match.group(1) if match else ref


def content_link(source: dict[str, Any], ref: str) -> tuple[str, str] | None:
    """``(artefact url, media)`` for one record's document, or ``None``.

    PDF first (a filed report), then the tagged / untagged XHTML report, then the RNS
    text. The link must be a plain ``NSM/`` path carrying the record's own id as a whole
    segment, so one record can never point the fetch at another record's document.
    """
    fmt = str(source.get("document_format") or "").strip().lower()
    candidates: list[tuple[str, str]] = []
    download = str(source.get("download_link") or "").strip()
    html_link = str(source.get("html_link") or "").strip()
    if download.lower().endswith(".pdf"):
        candidates.append((download, "pdf"))
    if fmt in ("tagged", "untagged") and html_link:
        candidates.append((html_link, "html"))
    if download.lower().endswith((".html", ".htm", ".xhtml")):
        candidates.append((download, "html"))
    for link, media in candidates:
        if ".." in link or not _NSM_LINK_RE.match(link):
            continue
        url = NSM_ARTEFACT_BASE + link
        if url_has_document_segment(url, ref):
            return url, media
    return None


async def _lei_from_entity_master(session: Any, company: Any) -> str | None:
    """The LEI the platform already holds for this company's legal entity, if any."""
    entity_id = getattr(company, "legal_entity_id", None)
    if session is None or entity_id is None:
        return None
    try:
        from sqlalchemy import select

        from app.models.legal_entity import EntityIdentifier

        async with session.begin_nested():
            value = (
                await session.execute(
                    select(EntityIdentifier.value_normalized)
                    .where(EntityIdentifier.legal_entity_id == entity_id,
                           EntityIdentifier.scheme == "lei")
                    .limit(2)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 - an unreadable entity master is no identity
        return None
    values = {str(v).upper() for v in value if v}
    if len(values) != 1:
        return None
    lei = values.pop()
    return lei if _LEI_RE.match(lei) else None


async def _lei_from_gleif(
    isin: str, exchange_name: str, *, cfg: Any, fetcher: Any
) -> tuple[str | None, str, int]:
    """``(lei, detail, requests)`` — accepted only on one unambiguous, agreeing record."""
    from app.services.discovery.directories import _names_agree
    from app.services.discovery.identity import normalised_name
    from app.services.sources.document_fetcher import safe_fetch_document

    result = await (fetcher or safe_fetch_document)(
        GLEIF_ISIN_URL.format(isin=isin), allowed_domains=(GLEIF_HOST,), cfg=cfg,
        resolve_ip=True,
        extra_text_content_types=("application/json", "application/vnd.api+json"),
    )
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        return None, "GLEIF could not be reached for this ISIN", 1
    try:
        records = json.loads(content).get("data") or []
    except (ValueError, AttributeError):
        return None, "GLEIF answered unreadably", 1
    if len(records) != 1:
        return None, f"GLEIF returned {len(records)} records for this ISIN, not one", 1
    record = records[0]
    lei = str(record.get("id") or "").upper()
    legal = str(
        ((record.get("attributes") or {}).get("entity") or {}).get("legalName", {}).get("name")
        or ""
    )
    if not _LEI_RE.match(lei):
        return None, "GLEIF's LEI is malformed", 1
    if not _names_agree(normalised_name(legal), normalised_name(exchange_name),
                        ticker_matched=True):
        return None, (f"GLEIF names the ISIN's issuer {legal!r}, which does not agree "
                      f"with the exchange's {exchange_name!r}"), 1
    return lei, f"LEI from GLEIF for ISIN {isin} ({legal})", 1


async def resolve_uk_issuer(
    session: Any, company: Any, *, cfg: Any, fetcher: Any = None
) -> tuple[VerifiedIssuer | None, str | None, int]:
    """``(issuer, why-not, requests)``. Fails closed at every step."""
    from app.services.discovery.directories import _strip_suffix, find_listing

    ticker = _strip_suffix(str(getattr(company, "ticker", "") or ""))
    row, reason = await find_listing(
        name=getattr(company, "name", None), ticker=ticker, venue=VENUE_LSE, cfg=cfg,
        fetcher=fetcher,
    )
    requests = 1
    if row is None:
        return None, reason, requests
    isin = str(row.isin or "").upper()
    if not _ISIN_RE.match(isin):
        return None, "the exchange's record states no valid ISIN", requests
    basis = f"LSE instrument {row.ticker} ({row.name}), ISIN {isin}"
    # GLEIF always decides; an LEI the platform already holds must AGREE with it, so a
    # wrong entity-master row can never point the listing at another issuer.
    lei, detail, n = await _lei_from_gleif(isin, row.name, cfg=cfg, fetcher=fetcher)
    requests += n
    if lei is None:
        return None, detail, requests
    held = await _lei_from_entity_master(session, company)
    if held and held != lei:
        return None, "the platform's entity master holds a different LEI than GLEIF", requests
    basis += f", {detail}"
    return VerifiedIssuer(
        company_id=company.id, ticker=row.ticker, venue=VENUE_LSE, name=row.name,
        source_id=SOURCE_UK_FCA_NSM, identity_basis=basis, isin=isin, lei=lei,
    ), None, requests


def _query(lei: str) -> dict[str, Any]:
    """The NSM search body: this LEI, newest first. Identifiers only — never a URL."""
    return {
        "from": 0,
        "size": NSM_PAGE_SIZE,
        "sort": "submitted_date",
        "sortorder": "desc",
        "criteriaObj": {
            "criteria": [
                {"name": "company_lei", "value": ["", lei, "disclose_org", "related_org"]}
            ],
            "dateCriteria": [],
        },
    }


def parse_nsm_hits(
    payload: dict[str, Any], issuer: VerifiedIssuer, *, lookback_days: int, now: datetime
) -> tuple[list[OfficialDocument], int]:
    """``(documents, refused)`` from one NSM response. Pure; never raises on shape."""
    hits = ((payload or {}).get("hits") or {}).get("hits") or []
    cutoff = now - timedelta(days=max(1, lookback_days))
    documents: list[OfficialDocument] = []
    refused = 0
    for hit in hits if isinstance(hits, list) else []:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            refused += 1
            continue
        # Another issuer's record never becomes this issuer's evidence.
        if str(source.get("lei") or "").upper() != issuer.lei:
            refused += 1
            continue
        # The NSM keeps superseded versions of an amended disclosure; only the latest
        # is current.
        if str(source.get("latest_flag") or "Y").upper() != "Y":
            refused += 1
            continue
        raw_ref = canonical_document_ref(source.get("disclosure_id") or source.get("seq_id"))
        # A corrected re-filing keeps the base id and its address, so it becomes a new
        # VERSION of the same document (new bytes, promoted current) — never a second one.
        ref = canonical_document_ref(base_document_ref(raw_ref)) if raw_ref else None
        published = _parse_time(source.get("publication_date") or source.get("submitted_date"))
        if ref is None or published is None:
            refused += 1
            continue
        if published < cutoff:
            continue
        link = content_link(source, ref)
        if link is None:
            refused += 1
            continue
        url, media = link
        headline = str(source.get("headline") or "").strip()[:300]
        nsm_type = str(source.get("type") or "").strip()[:120]
        category, doc_kind, rank = classify_uk(
            nsm_type=nsm_type, headline=headline,
            document_format=str(source.get("document_format") or ""),
        )
        documents.append(OfficialDocument(
            source_id=SOURCE_UK_FCA_NSM, document_ref=ref, published_at=published,
            headline=headline, venue_category=nsm_type, category=category,
            doc_kind=doc_kind, research_rank=rank, price_sensitive=None, media=media,
            official_url=url, content_url=url,
            updated_at=_parse_time(source.get("last_updated_date")),
        ))
    return documents, refused


async def list_uk_disclosures(
    session: Any, company: Any, *, cfg: Any, fetcher: Any = None, poster: Any = None,
    now: datetime | None = None,
) -> DisclosureListing:
    """One issuer's recent NSM disclosures, or why there are none. Never raises."""
    from app.services.sources.document_fetcher import safe_post_json

    try:
        issuer, why, requests = await resolve_uk_issuer(session, company, cfg=cfg,
                                                         fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001 - identity failing is identity unverified
        return DisclosureListing(issuer=None, reason=REASON_IDENTITY_UNVERIFIED,
                                 detail=type(exc).__name__)
    if issuer is None:
        return DisclosureListing(
            issuer=None,
            reason=REASON_IDENTITY_UNVERIFIED if why else REASON_SOURCE_UNAVAILABLE,
            detail=why or "the London Stock Exchange's own record could not be read",
            requests=requests)
    result = await (poster or safe_post_json)(
        NSM_SEARCH_URL, _query(issuer.lei or ""), allowed_domains=(NSM_SEARCH_HOST,),
        cfg=cfg,
    )
    requests += 1
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail=getattr(result, "error", None), requests=requests)
    try:
        payload = json.loads(content)
    except ValueError:
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail="the NSM answered unreadably", requests=requests)
    documents, refused = parse_nsm_hits(
        payload, issuer,
        lookback_days=int(getattr(cfg, "v3_disclosure_lookback_days", 540) or 540),
        now=now or datetime.now(timezone.utc),
    )
    return DisclosureListing(
        issuer=issuer, documents=documents, requests=requests, refused=refused,
        reason=None if documents else REASON_DATA_NOT_SOURCED,
    )


__all__ = [
    "NSM_ARTEFACT_HOST",
    "base_document_ref",
    "content_link",
    "list_uk_disclosures",
    "parse_nsm_hits",
    "resolve_uk_issuer",
]
