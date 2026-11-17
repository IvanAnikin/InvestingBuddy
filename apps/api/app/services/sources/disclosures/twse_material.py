"""TWSE material announcements (重大訊息) — the issuer's own material information.

V3.20 slice 11.4. Every TWSE-listed company must publish material information (重大訊息)
through MOPS under TWSE's rules; TWSE's OpenAPI republishes each day's announcements as
open data: ``t187ap04_L`` (上市公司每日重大訊息).

THE SOURCE
==========
One JSON array, about 100–200 rows, each a COMPLETE announcement: company code
(公司代號) and short name (公司名稱), announcement date and time (發言日期/發言時間, ROC
calendar, Asia/Taipei), the clause of the material-information rules it was filed under
(符合條款), the fact date (事實發生日), the subject (主旨) and the full body (說明). The
announcement's text IS the row: there is no PDF to fetch.

The feed covers ONE announcement day (measured 2026-11-17: 115 rows, all dated
1151116). So every read is HELD (``twse_material_announcements``, migration 061): a
listing refreshes the feed (one request, at most once per ``_REFRESH_EVERY`` per
process) and then lists the held rows in the lookback window. History begins with the
first read. A day no read reached is a hole, and absence from the held rows is never
evidence that an issuer announced nothing.

IDENTITY
========
The issuer is verified on TWSE's own listed-company directory (``t187ap03_L``, the 11.1
directory): its code AND an agreeing name, by ``directories.find_listing``. An
announcement is attributed by the feed's own ``公司代號`` field only — never by a name in
the text. The rendered document carries the code and TWSE's short name in its header,
so the shared content check (``acquisition.document_names_issuer``) reads the name TWSE
gave the row.

CITATIONS
=========
The open data has no per-announcement address. The citation opens the feed
(``openapi.twse.com.tw/v1/opendata/t187ap04_L?announcement=<ref>``; the API ignores the
parameter, the corpus reads it as the document id). After its day the feed no longer
shows the announcement: the held row and the raw artifact are the record of what it said.

TERMS
=====
TWSE OpenAPI is TWSE's published machine interface; its data is licensed under the
Open Government Data License, version 1.0 (data.gov.tw/license): any use, royalty-free,
attribution required — ``ATTRIBUTION``. MOPS itself is not read: its pages are not open
data, and the OpenAPI is the agreed method.

NOT HERE: TPEx (OTC-listed) companies — TPEx publishes its own open data, not read yet.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.sources.disclosures.model import (
    RANK_MATERIAL,
    REASON_CONNECTOR_DISABLED,
    REASON_DATA_NOT_SOURCED,
    REASON_IDENTITY_UNVERIFIED,
    REASON_SOURCE_UNAVAILABLE,
    SOURCE_TWSE_MATERIAL,
    VENUE_TWSE,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.disclosures.transport import guarded_get

logger = logging.getLogger(__name__)

Getter = Callable[..., Awaitable[tuple[int | None, bytes | None, str | None]]]

TWSE_OPENAPI_HOST = "openapi.twse.com.tw"
FEED_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
ATTRIBUTION = ("Source: Taiwan Stock Exchange open data, 上市公司每日重大訊息 "
               "(Open Government Data License v1.0)")

#: The measured feed is ~170 KB; a 3 MB cap leaves room for a heavy day.
MAX_FEED_BYTES = 3_000_000
_MAX_SUBJECT_CHARS = 2_000
_MAX_BODY_CHARS = 30_000
_MAX_NAME_CHARS = 100
_TPE = ZoneInfo("Asia/Taipei")
#: A TWSE code: four digits, optionally a suffix (``00878``, ``910322``, ``2881A``).
_CODE_RE = re.compile(r"^[0-9]{4}[0-9A-Z]{0,2}$")
_ROC_DATE_RE = re.compile(r"^([0-9]{2,3})([0-9]{2})([0-9]{2})$")
_TIME_RE = re.compile(r"^[0-9]{1,6}$")
_CJK_WRAP_RE = re.compile(r"(?<=[^\x00-\x7f])[ \t]*\n\s*(?=[^\x00-\x7f])")
_REF_RE = re.compile(r"^[0-9A-Z]{4,6}-[0-9]{8}-[0-9]{6}-[0-9a-f]{10}$")

#: How often one process re-reads the feed. It changes once a day; a run over several
#: Taiwanese companies must not read it once per company.
_REFRESH_EVERY = 3600.0
_last_refresh: dict[str, float] = {}


def is_enabled(cfg: Any) -> bool:
    """The connector and the directory its identity check reads must both be on."""
    return bool(getattr(cfg, "v3_twse_material_info_enabled", False)
                and getattr(cfg, "v3_directory_twse_enabled", False))


# ── Parsing the feed ───────────────────────────────────────────────────────── #


def roc_date(value: Any) -> date | None:
    """``1151116`` (ROC year 115) → 2026-11-16; anything else → None."""
    found = _ROC_DATE_RE.match(str(value or "").strip())
    if not found:
        return None
    try:
        return date(int(found.group(1)) + 1911, int(found.group(2)), int(found.group(3)))
    except ValueError:
        return None


def announcement_time(value: Any) -> tuple[int, int, int] | None:
    """``70003`` → (7, 0, 3); ``92016`` → (9, 20, 16). The feed drops leading zeros."""
    text = str(value or "").strip()
    if not _TIME_RE.match(text):
        return None
    text = text.zfill(6)
    hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def _clean(value: Any, limit: int) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return text.strip()[:limit]


@dataclass(frozen=True)
class Announcement:
    """One material announcement exactly as the feed states it."""

    code: str
    company_name: str
    announced_at: datetime
    clause: str
    fact_date: date | None
    subject: str
    body: str
    digest: str

    @property
    def ref(self) -> str:
        local = self.announced_at.astimezone(_TPE)
        return f"{self.code}-{local:%Y%m%d}-{local:%H%M%S}-{self.digest[:10]}"

    @property
    def headline(self) -> str:
        """The subject on one line — metadata, never cited as content. The feed wraps
        Chinese text at a fixed width; a wrap between two non-ASCII characters is not a
        space."""
        joined = _CJK_WRAP_RE.sub("", self.subject)
        return re.sub(r"\s+", " ", joined).strip()[:300]


def _digest(code: str, when: datetime, subject: str, body: str) -> str:
    material = "\x1f".join((code, when.isoformat(), subject, body))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_row(item: Any) -> Announcement | None:
    """One feed row, or None when it cannot be attributed and dated."""
    if not isinstance(item, dict):
        return None
    # The feed's own keys carry stray spaces ("主旨 ", measured).
    row = {str(k).strip(): v for k, v in item.items()}
    code = str(row.get("公司代號") or "").strip().upper()
    name = _clean(row.get("公司名稱"), _MAX_NAME_CHARS)
    day = roc_date(row.get("發言日期"))
    clock = announcement_time(row.get("發言時間"))
    subject = _clean(row.get("主旨"), _MAX_SUBJECT_CHARS)
    body = _clean(row.get("說明"), _MAX_BODY_CHARS)
    if not (_CODE_RE.match(code) and name and day and clock and (subject or body)):
        return None
    when = datetime(day.year, day.month, day.day, *clock, tzinfo=_TPE).astimezone(timezone.utc)
    return Announcement(
        code=code, company_name=name, announced_at=when,
        clause=_clean(row.get("符合條款"), 40), fact_date=roc_date(row.get("事實發生日")),
        subject=subject, body=body, digest=_digest(code, when, subject, body))


def parse_feed(payload: bytes | None) -> tuple[list[Announcement] | None, int]:
    """``(announcements, refused)``; ``None`` when the payload is not the feed."""
    if not payload:
        return None, 0
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None, 0
    if not isinstance(data, list):
        return None, 0
    out: dict[str, Announcement] = {}
    refused = 0
    for item in data:
        parsed = parse_row(item)
        if parsed is None:
            refused += 1
            continue
        out[parsed.ref] = parsed
    return list(out.values()), refused


# ── Holding the feed ───────────────────────────────────────────────────────── #


async def fetch_feed(cfg: Any, *, getter: Getter | None = None
                     ) -> tuple[list[Announcement] | None, int, str | None]:
    """``(announcements, refused, why-not)`` from ONE read of the feed. Never raises."""
    try:
        _status, body, error = await (getter or guarded_get)(
            FEED_URL, cfg=cfg, max_bytes=MAX_FEED_BYTES, allowed_host=TWSE_OPENAPI_HOST,
            headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return None, 0, type(exc).__name__
    rows, refused = parse_feed(body)
    if rows is None:
        return None, 0, error or "unreadable"
    return rows, refused, None


async def store_announcements(session: Any, rows: list[Announcement], *,
                              now: datetime) -> int:
    """Hold the rows not yet held; returns how many were new. Never rewrites a row."""
    import sqlalchemy as sa

    from app.models.twse_material_announcement import TwseMaterialAnnouncement

    if not rows:
        return 0
    refs = [r.ref for r in rows]
    held = set((await session.execute(
        sa.select(TwseMaterialAnnouncement.ref)
        .where(TwseMaterialAnnouncement.ref.in_(refs)))).scalars())
    new = [r for r in rows if r.ref not in held]
    for row in new:
        session.add(TwseMaterialAnnouncement(
            ref=row.ref, company_code=row.code, company_name=row.company_name,
            announced_at=row.announced_at, clause=row.clause, fact_date=row.fact_date,
            subject=row.subject, body=row.body, content_sha256=row.digest, fetched_at=now))
    if new:
        await session.flush()
    return len(new)


async def refresh_feed(session: Any, *, cfg: Any, getter: Getter | None = None,
                       now: datetime | None = None, force: bool = False
                       ) -> dict[str, Any]:
    """Read the feed and hold its new rows, unless this process read it recently.

    ``{"read": bool, "stored": int, "rows": int, "refused": int, "error": str|None}``.
    Never raises; a failed read leaves the held rows as they were.
    """
    clock = time.monotonic()
    last = _last_refresh.get(FEED_URL)
    if not force and last is not None and clock - last < _REFRESH_EVERY:
        return {"read": False, "stored": 0, "rows": 0, "refused": 0, "error": None,
                "skipped": "read recently"}
    rows, refused, why = await fetch_feed(cfg, getter=getter)
    if rows is None:
        return {"read": False, "stored": 0, "rows": 0, "refused": 0, "error": why}
    try:
        async with session.begin_nested():
            stored = await store_announcements(
                session, rows, now=now or datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("twse_material: holding the feed failed (%s)", type(exc).__name__)
        return {"read": True, "stored": 0, "rows": len(rows), "refused": refused,
                "error": "store_failed"}
    _last_refresh[FEED_URL] = clock
    return {"read": True, "stored": stored, "rows": len(rows), "refused": refused,
            "error": None}


def _from_model(model: Any) -> Announcement:
    return Announcement(
        code=model.company_code, company_name=model.company_name,
        announced_at=_aware(model.announced_at), clause=model.clause or "",
        fact_date=model.fact_date, subject=model.subject, body=model.body,
        digest=model.content_sha256)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def held_announcements(session: Any, code: str, *, since: datetime,
                             limit: int = 200) -> list[Announcement]:
    """One company's held announcements since ``since``, newest first."""
    import sqlalchemy as sa

    from app.models.twse_material_announcement import TwseMaterialAnnouncement

    found = (await session.execute(
        sa.select(TwseMaterialAnnouncement)
        .where(TwseMaterialAnnouncement.company_code == code,
               TwseMaterialAnnouncement.announced_at >= since)
        .order_by(TwseMaterialAnnouncement.announced_at.desc())
        .limit(limit))).scalars()
    return [_from_model(m) for m in found]


async def held_announcement(session: Any, ref: str) -> Announcement | None:
    from app.models.twse_material_announcement import TwseMaterialAnnouncement

    if not _REF_RE.match(ref or ""):
        return None
    model = await session.get(TwseMaterialAnnouncement, ref)
    return _from_model(model) if model is not None else None


# ── Identity and listing ───────────────────────────────────────────────────── #


def twse_code(ticker: str | None) -> str:
    """``2330`` / ``2330.TW`` / ``TWSE:2330`` → ``2330``; not a TWSE code → ``""``."""
    from app.services.discovery.directories import _strip_suffix

    code = _strip_suffix(str(ticker or "")).upper()
    return code if _CODE_RE.match(code) else ""


async def resolve_twse_issuer(
    session: Any, company: Any, *, cfg: Any, fetcher: Any = None,
) -> tuple[VerifiedIssuer | None, str | None]:
    """``(issuer, why-not)``; ``why`` None with no issuer means the directory could not
    be read (a source problem, not a verdict about the issuer)."""
    from app.services.discovery.directories import find_listing

    ticker, name = getattr(company, "ticker", None), getattr(company, "name", None)
    code = twse_code(ticker)
    if not code:
        return None, "the ticker is not a TWSE code"
    listing, why = await find_listing(name=name, ticker=code, venue=VENUE_TWSE, cfg=cfg,
                                      fetcher=fetcher)
    if listing is None:
        return None, why
    import sqlalchemy as sa

    from app.models.twse_material_announcement import TwseMaterialAnnouncement

    local = (await session.execute(
        sa.select(TwseMaterialAnnouncement.company_name)
        .where(TwseMaterialAnnouncement.company_code == listing.ticker)
        .order_by(TwseMaterialAnnouncement.announced_at.desc()).limit(1))).scalar()
    return VerifiedIssuer(
        company_id=company.id, ticker=listing.ticker, venue=VENUE_TWSE, name=listing.name,
        source_id=SOURCE_TWSE_MATERIAL,
        identity_basis=f"TWSE listed-company directory (t187ap03_L): code "
                       f"{listing.ticker} ({listing.name})",
        local_name=local or None, source_ref=listing.ticker,
    ), None


def official_url(ref: str) -> str:
    """The citation address: the feed, naming the announcement by its ref (the corpus
    recognises a document by a named id parameter, never a substring)."""
    return f"{FEED_URL}?announcement={ref}"


def to_document(row: Announcement) -> OfficialDocument:
    from app.services.sources.disclosure_events import classify_event
    from app.services.sources.document_discovery import DOC_KIND_OTHER

    label = f"重大訊息 {row.clause}".strip()
    url = official_url(row.ref)
    return OfficialDocument(
        source_id=SOURCE_TWSE_MATERIAL, document_ref=row.ref,
        published_at=row.announced_at, headline=row.headline, venue_category=label,
        category=classify_event(None, row.headline), doc_kind=DOC_KIND_OTHER,
        research_rank=RANK_MATERIAL, price_sensitive=None, media="html",
        official_url=url, content_url=url)


async def list_twse_announcements(
    session: Any, company: Any, *, cfg: Any, getter: Getter | None = None,
    fetcher: Any = None, now: datetime | None = None,
) -> DisclosureListing:
    """One issuer's held TWSE material announcements in the window. Never raises."""
    if not is_enabled(cfg):
        return DisclosureListing(issuer=None, reason=REASON_CONNECTOR_DISABLED,
                                 detail="TWSE material information or the TWSE directory "
                                        "is off")
    now = now or datetime.now(timezone.utc)
    refresh = await refresh_feed(session, cfg=cfg, getter=getter, now=now)
    requests = 1 if refresh["read"] or refresh["error"] else 0
    pages_failed = 1 if refresh["error"] else 0
    try:
        issuer, why = await resolve_twse_issuer(session, company, cfg=cfg, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=None, reason=REASON_IDENTITY_UNVERIFIED,
                                 detail=type(exc).__name__, requests=requests + 1,
                                 pages_failed=pages_failed)
    if issuer is None or not issuer.source_ref:
        return DisclosureListing(
            issuer=None,
            reason=REASON_IDENTITY_UNVERIFIED if why else REASON_SOURCE_UNAVAILABLE,
            detail=why or "the TWSE listed-company directory could not be read",
            requests=requests + 1, pages_failed=pages_failed)
    lookback = max(1, int(getattr(cfg, "v3_disclosure_lookback_days", 560) or 560))
    try:
        rows = await held_announcements(session, issuer.source_ref,
                                        since=now - timedelta(days=lookback))
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail=type(exc).__name__, requests=requests + 1,
                                 pages_failed=pages_failed)
    documents = [to_document(r) for r in rows]
    detail = None
    if refresh["error"]:
        detail = f"today's TWSE feed could not be read ({refresh['error']}); held rows only"
    elif not documents:
        detail = ("no held TWSE material announcement for this code; the feed covers one "
                  "day, so history starts with the first read")
    return DisclosureListing(
        issuer=issuer, documents=documents, requests=requests + 1, detail=detail,
        refused=refresh.get("refused", 0), pages_failed=pages_failed,
        reason=None if documents else REASON_DATA_NOT_SOURCED)


# ── The document: the held row → HTML → the shared extractor ───────────────── #


class TwseAnnouncementUnavailable(Exception):
    pass


class TwseIdentityMismatch(Exception):
    pass


def announcement_html(row: Announcement) -> str:
    """The announcement as a plain HTML page — every field escaped, nothing added.

    Two paragraphs: the stated fields with the subject, then the whole body. The shared
    HTML extractor keeps no paragraph under 40 characters, and one line of Chinese is
    usually shorter, so a paragraph per line would lose most of the announcement. The
    extractor collapses whitespace inside a paragraph, so nothing is merged that the
    feed kept apart beyond its line breaks.
    """
    esc = html.escape
    local = row.announced_at.astimezone(_TPE)
    fields = [f"公司代號：{esc(row.code)}", f"公司名稱：{esc(row.company_name)}",
              f"發言日期：{local:%Y-%m-%d} {local:%H:%M:%S}（臺北時間）"]
    if row.clause:
        fields.append(f"符合條款：{esc(row.clause)}")
    if row.fact_date:
        fields.append(f"事實發生日：{row.fact_date.isoformat()}")
    fields.append(f"主旨：{esc(_CJK_WRAP_RE.sub('', row.subject))}")
    body = " ".join(esc(line.strip()) for line in row.body.split("\n") if line.strip())
    return ("<html><head><meta charset=\"utf-8\"><title>"
            f"{esc(row.headline)}</title></head><body>"
            f"<h1>{esc(row.company_name)}（{esc(row.code)}）重大訊息</h1>"
            f"<p>{'　'.join(fields)}</p><h2>說明</h2><p>{body}</p></body></html>")


def document_extractor(document: OfficialDocument, issuer: VerifiedIssuer, *,
                       session: Any) -> Callable[..., Awaitable[Any]]:
    """An extractor with ``live_primary_document_extractor``'s signature that reads
    THIS announcement from the held rows instead of fetching ``url``."""

    async def _extract(url: str, *, allowed_domains: tuple[str, ...],
                       title_hint: str | None = None, issuer_context: Any = None,
                       cfg: Any = None, period_policy: str | None = None,
                       published_at: date | None = None, **_: Any) -> Any:
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        started = time.perf_counter()
        row = await held_announcement(session, document.document_ref)
        if row is None:
            raise TwseAnnouncementUnavailable("not_held")
        if row.code != issuer.source_ref:
            raise TwseIdentityMismatch("code_differs")
        page = announcement_html(row)
        fetched = DocumentFetchResult(
            requested_url=document.official_url, final_url=document.official_url,
            status_code=200, content_type="text/html", document_type="html",
            content=page.encode("utf-8"))
        return await live_fetchers._artifact_from_fetch(
            fetched, title=title_hint, original_language="zh",
            issuer_context=issuer_context, cfg=cfg,
            fetch_ms=int((time.perf_counter() - started) * 1000),
            period_policy=period_policy, published_at=published_at)

    return _extract


__all__ = [
    "ATTRIBUTION",
    "Announcement",
    "FEED_URL",
    "MAX_FEED_BYTES",
    "TWSE_OPENAPI_HOST",
    "TwseAnnouncementUnavailable",
    "TwseIdentityMismatch",
    "announcement_html",
    "announcement_time",
    "document_extractor",
    "fetch_feed",
    "held_announcement",
    "held_announcements",
    "is_enabled",
    "list_twse_announcements",
    "official_url",
    "parse_feed",
    "parse_row",
    "refresh_feed",
    "resolve_twse_issuer",
    "roc_date",
    "store_announcements",
    "to_document",
    "twse_code",
]
