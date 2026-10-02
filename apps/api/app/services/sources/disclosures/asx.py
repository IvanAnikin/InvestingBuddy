"""ASX company announcements — the listing, and the attached document itself.

IDENTITY
========
An ASX code is the exchange's own identifier for a listing, and the code is taken from
the V3.19-verified directory row for this company (``find_listing`` on the ASX's own
official list, name agreeing) — never guessed from a name. Every fetched document must
then NAME the issuer (``acquisition.document_names_issuer``) before it is attributed.

Measured hazard: the middle of an ASX research-API document key is NOT an ``idsId``;
used as one it returned a Viva Energy PDF for an EcoGraf request. So the listing used
here is the ASX's own announcements page for the code, whose links carry the real
``idsId`` of each announcement.

LISTING
=======
``https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=<CODE>
&timeframe=Y&year=<YYYY>`` — one year per request; the current and the previous year are
read. Each row: date and time, the exchange's price-sensitive marker, the headline, and
the ``displayAnnouncement.do?display=pdf&idsId=<id>`` link.

CONTENT
=======
The announcement's own address (``displayAnnouncement.do?display=pdf&idsId=<id>``) is
what a citation opens. Its page carries the attached document's address in a hidden
``pdfURL`` field, accepted only when it is exactly
``https://announcements.asx.com.au/asxpdf/<8 digits>/pdf/<id>.pdf``; that PDF is what is
fetched, extracted and stored. The headline is never content.

TERMS: announcements are for private and personal use; commercial use requires ASX
authority. See ``docs/non-us-primary-documents.md``.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.sources.disclosures.model import (
    REASON_DATA_NOT_SOURCED,
    REASON_IDENTITY_UNVERIFIED,
    REASON_SOURCE_UNAVAILABLE,
    SOURCE_ASX_ANNOUNCEMENTS,
    VENUE_ASX,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.disclosures.relevance import classify_asx

ASX_HOST = "www.asx.com.au"
ASX_PDF_HOST = "announcements.asx.com.au"
ASX_LISTING_URL = (
    "https://www.asx.com.au/asx/v2/statistics/announcements.do"
    "?by=asxCode&asxCode={code}&timeframe=Y&year={year}"
)
ASX_DISPLAY_URL = (
    "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
    "?display=pdf&idsId={ids_id}"
)
_ASX_TZ = ZoneInfo("Australia/Sydney")

_CODE_RE = re.compile(r"^[A-Z0-9]{3,6}$")
_IDS_ID_RE = re.compile(r"^[0-9]{6,10}$")
#: Bounds that keep parsing linear whatever the page holds: the page is cut to this
#: many bytes, and a row longer than this is not a listing row.
MAX_LISTING_BYTES = 2_000_000
_MAX_ROW_CHARS = 20_000
_CELL_RE = re.compile(r"<td[^>]{0,200}>(.{0,8000}?)</td>", re.S | re.I)
_LINK_RE = re.compile(
    r'href="/asx/v2/statistics/displayAnnouncement\.do\?display=pdf&(?:amp;)?idsId=([0-9]{6,10})"',
    re.I,
)
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)", re.I)
_PAGES_RE = re.compile(r"(\d+)\s+pages?", re.I)
#: The only attached-document address accepted from a display page.
PDF_URL_RE = re.compile(
    r"^https://announcements\.asx\.com\.au/asxpdf/[0-9]{8}/pdf/[a-z0-9]{6,40}\.pdf$")
_PDF_FIELD_RE = re.compile(r'name="pdfURL"\s{1,5}value="([^"]{1,300})"', re.I)


def _strip_tags(fragment: str) -> str:
    """Tag removal in one linear pass (a regex over unbalanced ``<`` is quadratic)."""
    out: list[str] = []
    depth = False
    for ch in fragment:
        if ch == "<":
            depth = True
        elif ch == ">" and depth:
            depth = False
            out.append(" ")
        elif not depth:
            out.append(ch)
    return "".join(out)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_strip_tags(fragment))).strip()


def _published(cell: str) -> datetime | None:
    date_m = _DATE_RE.search(cell)
    if not date_m:
        return None
    day, month, year = (int(x) for x in date_m.groups())
    hour = minute = 0
    time_m = _TIME_RE.search(cell)
    if time_m:
        hour, minute = int(time_m.group(1)) % 12, int(time_m.group(2))
        if time_m.group(3).lower() == "pm":
            hour += 12
    try:
        local = datetime(year, month, day, hour, minute, tzinfo=_ASX_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def parse_asx_listing(page: str) -> tuple[list[dict[str, Any]], int]:
    """``(rows, refused)`` from one yearly announcements page. Pure; never raises."""
    rows: list[dict[str, Any]] = []
    refused = 0
    # Split, don't match: rows are the text between "</tr>" boundaries, each bounded.
    for chunk in (page or "")[:MAX_LISTING_BYTES].split("</tr>"):
        start = chunk.rfind("<tr")
        row = chunk[start:] if start >= 0 else ""
        if not row or len(row) > _MAX_ROW_CHARS:
            continue
        link = _LINK_RE.search(row)
        if not link:
            continue  # header or spacer rows
        cells = _CELL_RE.findall(row)
        if len(cells) < 3:
            refused += 1
            continue
        published = _published(cells[0])
        ids_id = link.group(1)
        headline_cell = cells[2]
        headline = _text(re.sub(r"<span[^>]*>.*?</span>", " ", headline_cell, flags=re.S))
        if published is None or not _IDS_ID_RE.match(ids_id) or not headline:
            refused += 1
            continue
        pages = _PAGES_RE.search(_text(headline_cell))
        rows.append({
            "ids_id": ids_id, "published_at": published, "headline": headline[:300],
            "price_sensitive": "pricesens" in cells[1] or "price sensitive" in cells[1].lower(),
            "pages": int(pages.group(1)) if pages else None,
        })
    return rows, refused


def pdf_url_from_display_page(page: str) -> str | None:
    """The attached document's address from a display page, or ``None``.

    Accepted only when it is exactly an ``announcements.asx.com.au/asxpdf/…pdf`` address —
    a display page can never point the fetch anywhere else.
    """
    match = _PDF_FIELD_RE.search((page or "")[:MAX_LISTING_BYTES])
    if not match:
        return None
    url = html.unescape(match.group(1)).strip()
    return url if PDF_URL_RE.match(url) else None


async def resolve_asx_issuer(
    company: Any, *, cfg: Any, fetcher: Any = None
) -> tuple[VerifiedIssuer | None, str | None, int]:
    from app.services.discovery.directories import _strip_suffix, find_listing

    code = _strip_suffix(str(getattr(company, "ticker", "") or ""))
    if not _CODE_RE.match(code):
        return None, "the ticker is not an ASX code", 0
    row, reason = await find_listing(
        name=getattr(company, "name", None), ticker=code, venue=VENUE_ASX, cfg=cfg,
        fetcher=fetcher,
    )
    if row is None:
        # ``reason`` None means the directory could not be READ — a source problem,
        # not a verdict about the issuer.
        return None, reason, 1
    return VerifiedIssuer(
        company_id=company.id, ticker=row.ticker, venue=VENUE_ASX, name=row.name,
        source_id=SOURCE_ASX_ANNOUNCEMENTS,
        identity_basis=f"ASX official list: {row.ticker} ({row.name})",
    ), None, 1


async def list_asx_announcements(
    session: Any, company: Any, *, cfg: Any, fetcher: Any = None,
    now: datetime | None = None,
) -> DisclosureListing:
    """One issuer's recent ASX announcements, or why there are none. Never raises."""
    from app.services.sources.document_fetcher import safe_fetch_document

    try:
        issuer, why, requests = await resolve_asx_issuer(company, cfg=cfg, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=None, reason=REASON_IDENTITY_UNVERIFIED,
                                 detail=type(exc).__name__)
    if issuer is None:
        return DisclosureListing(
            issuer=None,
            reason=REASON_IDENTITY_UNVERIFIED if why else REASON_SOURCE_UNAVAILABLE,
            detail=why or "the ASX's own list could not be read", requests=requests)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(
        days=max(1, int(getattr(cfg, "v3_disclosure_lookback_days", 560) or 560)))
    documents: list[OfficialDocument] = []
    refused = 0
    reached = False
    # EVERY calendar year the window touches — a 540-day window read in February spans
    # three, and the middle one holds the latest annual and half-year reports.
    for year in range(now.year, cutoff.year - 1, -1):
        result = await (fetcher or safe_fetch_document)(
            ASX_LISTING_URL.format(code=issuer.ticker, year=year),
            allowed_domains=(ASX_HOST,), cfg=cfg, resolve_ip=True,
        )
        requests += 1
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        if not content:
            continue
        reached = True
        import asyncio

        page = content[:MAX_LISTING_BYTES].decode("utf-8", "replace")
        rows, bad = await asyncio.to_thread(parse_asx_listing, page)
        refused += bad
        for row in rows:
            if row["published_at"] < cutoff:
                continue
            category, doc_kind, rank = classify_asx(
                headline=row["headline"], price_sensitive=row["price_sensitive"])
            documents.append(OfficialDocument(
                source_id=SOURCE_ASX_ANNOUNCEMENTS, document_ref=row["ids_id"],
                published_at=row["published_at"], headline=row["headline"],
                venue_category="price sensitive" if row["price_sensitive"] else "",
                category=category, doc_kind=doc_kind, research_rank=rank,
                price_sensitive=row["price_sensitive"], media="pdf",
                official_url=ASX_DISPLAY_URL.format(ids_id=row["ids_id"]),
                content_url=None, pages=row["pages"],
            ))
    if not reached:
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail="the ASX announcements page could not be read",
                                 requests=requests)
    # Newest first, one row per announcement.
    unique = {d.document_ref: d for d in documents}
    ordered = sorted(unique.values(), key=lambda d: d.published_at or now, reverse=True)
    return DisclosureListing(
        issuer=issuer, documents=ordered, requests=requests, refused=refused,
        reason=None if ordered else REASON_DATA_NOT_SOURCED,
    )


__all__ = [
    "ASX_HOST",
    "ASX_PDF_HOST",
    "PDF_URL_RE",
    "list_asx_announcements",
    "parse_asx_listing",
    "pdf_url_from_display_page",
    "resolve_asx_issuer",
]
