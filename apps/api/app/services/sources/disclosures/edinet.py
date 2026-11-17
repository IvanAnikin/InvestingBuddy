"""EDINET (Japan FSA) — the issuer's own statutory filings, as official PDFs.

V3.20 slice 11.2. EDINET is the Financial Services Agency's electronic disclosure
system: every listed Japanese company files its annual securities report (有価証券報告書),
its half-year report (半期報告書; quarterly reports, 四半期報告書, until April 2024) and its
extraordinary reports (臨時報告書) there.

IDENTITY
========
The FSA's EDINET code list (``Edinetcode.zip``, public, no key) states for every filer
its EDINET code, whether it is listed, its Japanese and English names and its five-
character securities code (the four-character TSE code plus a check character). A
company is matched on its TSE code AND an English name that agrees, exactly as the
V3.19 exchange directories match (``find_listing``): a ticker alone never decides. A
filing is attributed only when its FILER is that EDINET code — a large-holding report
filed by a fund ABOUT the issuer is not the issuer's filing. Every fetched document must
then name the issuer (``acquisition.document_names_issuer``): its English name, or its
official Japanese name as the code list writes it.

LISTING
=======
EDINET lists filings by day only (``/api/v2/documents.json?date=…&type=2``). The days of
the lookback window are read into ``edinet_filings`` / ``edinet_index_days`` (bounded per
run by ``V3_EDINET_INDEX_DAYS_PER_RUN``; ``scripts/edinet_index_backfill.py`` fills the
window ahead of time), and an issuer's listing is a query on its EDINET code. A window
day that has not been read is a HOLE, reported as ``pages_failed``: a listing with a hole
is not evidence that a report is absent. Withdrawn filings are never listed.

THE KEY
=======
The document list needs the user's EDINET API key. It is sent as the
``Ocp-Apim-Subscription-Key`` request HEADER (measured: accepted, 2026-11-17), never as
the ``Subscription-Key`` query parameter, so no URL this module builds, stores or logs
ever carries it. The code list and the PDFs are public and are fetched without it.

CONTENT
=======
``https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/<docID>.pdf`` — the official
PDF of the filing, public. It is what a citation opens and what is fetched, extracted
and indexed. The listing's description is metadata, never content.

TERMS: EDINET information is published by the FSA for reuse; cite "EDINET" (金融庁) as
the source. The API must not be loaded excessively: day listings are read one at a time.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.sources.disclosures.model import (
    RANK_ADMINISTRATIVE,
    RANK_ANNUAL,
    RANK_INTERIM,
    RANK_MATERIAL,
    RANK_ORDINARY,
    RANK_PERIODIC,
    REASON_CONNECTOR_DISABLED,
    REASON_DATA_NOT_SOURCED,
    REASON_IDENTITY_UNVERIFIED,
    REASON_SOURCE_UNAVAILABLE,
    SOURCE_EDINET,
    VENUE_TSE,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.disclosures.transport import guarded_get

logger = logging.getLogger(__name__)

EDINET_API_HOST = "api.edinet-fsa.go.jp"
EDINET_DL_HOST = "disclosure2dl.edinet-fsa.go.jp"
DOCUMENT_LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
CODE_LIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
PDF_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/{doc_id}.pdf"
#: The request header carrying the key (Azure API Management's subscription header).
KEY_HEADER = "Ocp-Apim-Subscription-Key"
ATTRIBUTION = "Source: EDINET, Financial Services Agency of Japan (金融庁)"

_JST = ZoneInfo("Asia/Tokyo")
_DOC_ID_RE = re.compile(r"^S[0-9A-Z]{7}$")
_EDINET_CODE_RE = re.compile(r"^E[0-9]{5}$")
#: A TSE listing code: four digits, or (since 2024) three digits and a letter ("130A").
_TSE_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{2}[0-9A-Z]$")
_SEC_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]$")

MAX_LIST_BYTES = 30_000_000
MAX_CODE_LIST_BYTES = 5_000_000
#: The code list's CSV, decompressed. Measured 2026-11-17: ~2.6 MB.
MAX_CODE_LIST_CSV_BYTES = 25_000_000
_CODE_LIST_TTL_SECONDS = 24 * 3600

# ── Document types (EDINET's ``docTypeCode``) ──────────────────────────────── #
DOC_ANNUAL = "120"            # 有価証券報告書
DOC_ANNUAL_AMENDED = "130"    # 訂正有価証券報告書
DOC_QUARTERLY = "140"         # 四半期報告書 (abolished from April 2024)
DOC_QUARTERLY_AMENDED = "150"
DOC_HALF_YEAR = "160"         # 半期報告書
DOC_HALF_YEAR_AMENDED = "170"
DOC_EXTRAORDINARY = "180"     # 臨時報告書
DOC_EXTRAORDINARY_AMENDED = "190"

#: ``docTypeCode`` → (English label, rank). Unlisted codes are ordinary filings.
_DOC_TYPES: dict[str, tuple[str, int]] = {
    DOC_ANNUAL: ("Annual Securities Report", RANK_ANNUAL),
    DOC_ANNUAL_AMENDED: ("Amended Annual Securities Report", RANK_PERIODIC),
    DOC_QUARTERLY: ("Quarterly Report", RANK_PERIODIC),
    DOC_QUARTERLY_AMENDED: ("Amended Quarterly Report", RANK_ORDINARY),
    DOC_HALF_YEAR: ("Half-year Report", RANK_INTERIM),
    DOC_HALF_YEAR_AMENDED: ("Amended Half-year Report", RANK_ORDINARY),
    DOC_EXTRAORDINARY: ("Extraordinary Report", RANK_MATERIAL),
    DOC_EXTRAORDINARY_AMENDED: ("Amended Extraordinary Report", RANK_ORDINARY),
    "030": ("Securities Registration Statement", RANK_MATERIAL),
    "135": ("Confirmation Letter", RANK_ADMINISTRATIVE),
    "136": ("Amended Confirmation Letter", RANK_ADMINISTRATIVE),
    "220": ("Status Report of Share Buy-backs", RANK_ADMINISTRATIVE),
    "230": ("Amended Status Report of Share Buy-backs", RANK_ADMINISTRATIVE),
    "235": ("Internal Control Report", RANK_ORDINARY),
    "236": ("Amended Internal Control Report", RANK_ADMINISTRATIVE),
    "350": ("Large Shareholding Report", RANK_ADMINISTRATIVE),
    "360": ("Amended Large Shareholding Report", RANK_ADMINISTRATIVE),
}

_QUARTER_RE = re.compile(r"第([1-4１-４])四半期")
_FULLWIDTH_DIGITS = str.maketrans("１２３４", "1234")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")

#: ``getter(url, params=..., headers=..., cfg=..., max_bytes=...)`` →
#: ``(http status or None, body or None, error code or None)``.
Getter = Callable[..., Awaitable[tuple[int | None, bytes | None, str | None]]]


def api_key(cfg: Any) -> str:
    return str(getattr(cfg, "edinet_api_key", "") or "").strip()


def is_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_edinet_disclosures_enabled", False)) and bool(api_key(cfg))


# ── Transport: ``transport.guarded_get`` (shared with OpenDART) ──────────────── #


# ── The code list (identity) ───────────────────────────────────────────────── #


@dataclass(frozen=True)
class EdinetCodeRow:
    edinet_code: str
    sec_code: str
    listed: bool
    name_ja: str
    name_en: str

    @property
    def tse_code(self) -> str:
        return self.sec_code[:4]


def parse_code_list_csv(text: str) -> list[EdinetCodeRow]:
    """Rows of the code list's CSV (decoded). Columns are found by their header names;
    a file whose header lacks them yields nothing. Pure; never raises."""
    reader = csv.reader(io.StringIO(text or ""))
    header: dict[str, int] | None = None
    rows: list[EdinetCodeRow] = []
    for record in reader:
        cells = [c.strip() for c in record]
        if header is None:
            if any("ＥＤＩＮＥＴコード" in c or c == "EDINETコード" for c in cells):
                header = {c: i for i, c in enumerate(cells)}
            continue

        def cell(*names: str, cells: list[str] = cells) -> str:
            for n in names:
                i = header.get(n) if header else None
                if i is not None and i < len(cells):
                    return cells[i]
            return ""

        code = cell("ＥＤＩＮＥＴコード", "EDINETコード")
        sec = cell("証券コード").upper()
        if not _EDINET_CODE_RE.match(code) or not _SEC_CODE_RE.match(sec):
            continue
        rows.append(EdinetCodeRow(
            edinet_code=code, sec_code=sec, listed=cell("上場区分") == "上場",
            name_ja=cell("提出者名")[:300], name_en=cell("提出者名（英字）")[:300]))
    return rows


def parse_code_list_zip(payload: bytes) -> list[EdinetCodeRow]:
    """The code list from its zip. Bounded: one CSV member, its declared AND actual
    decompressed size capped. Never raises."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith(".csv")]
            if len(members) != 1 or members[0].file_size > MAX_CODE_LIST_CSV_BYTES:
                return []
            with archive.open(members[0]) as handle:
                raw = handle.read(MAX_CODE_LIST_CSV_BYTES + 1)
            if len(raw) > MAX_CODE_LIST_CSV_BYTES:
                return []
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return []
    return parse_code_list_csv(raw.decode("cp932", "replace"))


_CODE_LIST_CACHE: dict[str, tuple[float, list[EdinetCodeRow]]] = {}


async def load_code_list(cfg: Any, *, getter: Getter | None = None) -> list[EdinetCodeRow]:
    """The code list, cached for a day per process. ``[]`` when it cannot be read."""
    cached = _CODE_LIST_CACHE.get("rows")
    if cached and time.monotonic() - cached[0] < _CODE_LIST_TTL_SECONDS:
        return cached[1]
    status, body, _error = await (getter or guarded_get)(
        CODE_LIST_URL, cfg=cfg, max_bytes=MAX_CODE_LIST_BYTES, allowed_host=EDINET_DL_HOST)
    if not body:
        return []
    rows = await asyncio.to_thread(parse_code_list_zip, body)
    if rows:
        _CODE_LIST_CACHE["rows"] = (time.monotonic(), rows)
    return rows


def match_issuer(
    rows: list[EdinetCodeRow], *, ticker: str | None, name: str | None,
) -> tuple[EdinetCodeRow | None, str]:
    """``(row, "")`` for the listed filer with this TSE code whose name agrees, else
    ``(None, why)``."""
    from app.services.discovery.directories import _acronym_agrees, _names_agree, _strip_suffix
    from app.services.discovery.identity import normalised_name

    code = _strip_suffix(str(ticker or "")).upper()
    if not _TSE_CODE_RE.match(code):
        return None, "the ticker is not a TSE code"
    hits = [r for r in rows if r.listed and r.tse_code == code]
    if not hits:
        return None, f"the EDINET code list has no listed filer with securities code {code}"
    wanted = normalised_name(name)
    for row in hits:
        if (not wanted or (row.name_en and _names_agree(
                wanted, normalised_name(row.name_en), ticker_matched=True))
                or _acronym_agrees(row.name_en, name) or (name or "").strip() == row.name_ja):
            return row, ""
    return None, (f"EDINET lists securities code {code} as {hits[0].name_en or hits[0].name_ja!r}, "
                  f"not {name!r}")


# ── The daily index ────────────────────────────────────────────────────────── #


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _submitted(value: Any) -> datetime | None:
    try:
        local = datetime.strptime(str(value), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    return local.replace(tzinfo=_JST).astimezone(timezone.utc)


def parse_document_list(payload: bytes | None) -> tuple[list[dict[str, Any]] | None, int]:
    """``(rows, refused)`` from one day's ``documents.json?type=2``; ``rows`` is None when
    the answer is not a successful listing. Pure; never raises."""
    try:
        data = json.loads((payload or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, 0
    if not isinstance(data, dict):
        return None, 0
    meta = data.get("metadata") or {}
    if str(meta.get("status") or "") != "200" or not isinstance(data.get("results"), list):
        return None, 0
    rows: list[dict[str, Any]] = []
    refused = 0
    for item in data["results"]:
        if not isinstance(item, dict):
            refused += 1
            continue
        doc_id = str(item.get("docID") or "")
        if not _DOC_ID_RE.match(doc_id):
            refused += 1
            continue
        edinet_code = str(item.get("edinetCode") or "") or None
        sec_code = str(item.get("secCode") or "").upper() or None
        rows.append({
            "doc_id": doc_id,
            "edinet_code": edinet_code if edinet_code and _EDINET_CODE_RE.match(edinet_code)
            else None,
            "sec_code": sec_code if sec_code and _SEC_CODE_RE.match(sec_code) else None,
            "filer_name": str(item.get("filerName") or "")[:300] or None,
            "doc_type_code": str(item.get("docTypeCode") or "")[:8] or None,
            "ordinance_code": str(item.get("ordinanceCode") or "")[:8] or None,
            "form_code": str(item.get("formCode") or "")[:16] or None,
            "doc_description": str(item.get("docDescription") or "")[:500] or None,
            "period_start": _date(item.get("periodStart")),
            "period_end": _date(item.get("periodEnd")),
            "submitted_at": _submitted(item.get("submitDateTime")),
            "parent_doc_id": str(item.get("parentDocID") or "")[:16] or None,
            "withdrawal_status": str(item.get("withdrawalStatus") or "")[:4] or None,
            "pdf_flag": str(item.get("pdfFlag") or "") == "1",
            "xbrl_flag": str(item.get("xbrlFlag") or "") == "1",
            "csv_flag": str(item.get("csvFlag") or "") == "1",
            "english_flag": str(item.get("englishDocFlag") or "") == "1",
        })
    return rows, refused


@dataclass
class IndexRefresh:
    days_read: int = 0
    days_failed: int = 0
    filings_seen: int = 0
    errors: list[str] = field(default_factory=list)


def today_jst(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_JST).date()


def window_days(now: datetime, lookback_days: int) -> list[date]:
    """Every calendar day of the window, newest first (today in Japan included)."""
    end = today_jst(now)
    return [end - timedelta(days=i) for i in range(max(1, lookback_days) + 1)]


async def _days_held(session: Any, days: list[date]) -> dict[date, Any]:
    import sqlalchemy as sa

    from app.models.edinet_filing import EdinetIndexDay

    if not days:
        return {}
    result = await session.execute(
        sa.select(EdinetIndexDay).where(EdinetIndexDay.day.in_(days)))
    return {row.day: row for row in result.scalars()}


async def _store_day(session: Any, day: date, rows: list[dict[str, Any]], *,
                     now: datetime) -> None:
    import sqlalchemy as sa

    from app.models.edinet_filing import EdinetFiling, EdinetIndexDay

    ids = [r["doc_id"] for r in rows]
    held: dict[str, Any] = {}
    for start in range(0, len(ids), 500):
        result = await session.execute(
            sa.select(EdinetFiling).where(EdinetFiling.doc_id.in_(ids[start:start + 500])))
        held.update({f.doc_id: f for f in result.scalars()})
    for row in rows:
        filing = held.get(row["doc_id"])
        if filing is None:
            filing = EdinetFiling(doc_id=row["doc_id"], index_day=day, created_at=now)
            session.add(filing)
            held[row["doc_id"]] = filing
        for key, value in row.items():
            if key != "doc_id":
                setattr(filing, key, value)
        filing.index_day = day
        filing.updated_at = now
    record = await session.get(EdinetIndexDay, day)
    if record is None:
        record = EdinetIndexDay(day=day, fetched_at=now, filings=len(rows), final=False)
        session.add(record)
    record.fetched_at = now
    record.filings = len(rows)
    # A day read after it ended (in Japan) can receive nothing more.
    record.final = today_jst(now) > day
    await session.flush()


async def refresh_index(
    session: Any, *, cfg: Any, now: datetime | None = None, max_days: int,
    lookback_days: int | None = None, getter: Getter | None = None, pause: float = 0.3,
) -> IndexRefresh:
    """Read up to ``max_days`` window days that are not yet held as final, newest first.

    A provisional day (read while it was still today in Japan) is read again. Each day
    is one request with the key in a header; a failed day stays a hole."""
    now = now or datetime.now(timezone.utc)
    out = IndexRefresh()
    key = api_key(cfg)
    if not key or max_days <= 0:
        return out
    lookback = int(lookback_days if lookback_days is not None
                   else getattr(cfg, "v3_disclosure_lookback_days", 560) or 560)
    days = window_days(now, lookback)
    held = await _days_held(session, days)
    todo = [d for d in days if not (d in held and held[d].final)][:max_days]
    for i, day in enumerate(todo):
        if i and pause > 0:
            await asyncio.sleep(pause)
        status, body, error = await (getter or guarded_get)(
            DOCUMENT_LIST_URL, params={"date": day.isoformat(), "type": "2"},
            headers={KEY_HEADER: key}, cfg=cfg, max_bytes=MAX_LIST_BYTES,
            allowed_host=EDINET_API_HOST)
        rows, _refused = parse_document_list(body) if body else (None, 0)
        if rows is None:
            out.days_failed += 1
            out.errors.append(error or f"unusable answer (http {status})")
            if status in (401, 403):
                break  # a refused key will be refused for every day
            continue
        try:
            async with session.begin_nested():
                await _store_day(session, day, rows, now=now)
        except Exception as exc:  # noqa: BLE001 - one day's write costs that day only
            logger.warning("edinet index day not stored: %s", type(exc).__name__)
            out.days_failed += 1
            out.errors.append(type(exc).__name__)
            continue
        out.days_read += 1
        out.filings_seen += len(rows)
    return out


# ── The listing ────────────────────────────────────────────────────────────── #


def _long_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def classify_filing(doc_type_code: str | None) -> tuple[str, str, int]:
    """``(category, doc_kind, rank)`` for an EDINET document type."""
    from app.services.sources.disclosure_events import (
        EVENT_CATEGORY_CAPITAL_STRUCTURE,
        EVENT_CATEGORY_GOVERNANCE,
        EVENT_CATEGORY_OTHER,
        EVENT_CATEGORY_RESULTS,
        EVENT_CATEGORY_SHAREHOLDING,
    )
    from app.services.sources.document_discovery import (
        DOC_KIND_ANNUAL_REPORT,
        DOC_KIND_INTERIM_REPORT,
        DOC_KIND_OTHER,
    )

    code = doc_type_code or ""
    rank = _DOC_TYPES.get(code, ("", RANK_ORDINARY))[1]
    if code == DOC_ANNUAL:
        return EVENT_CATEGORY_RESULTS, DOC_KIND_ANNUAL_REPORT, rank
    if code in (DOC_HALF_YEAR, DOC_QUARTERLY):
        return EVENT_CATEGORY_RESULTS, DOC_KIND_INTERIM_REPORT, rank
    if code in (DOC_ANNUAL_AMENDED, DOC_HALF_YEAR_AMENDED, DOC_QUARTERLY_AMENDED):
        return EVENT_CATEGORY_RESULTS, DOC_KIND_OTHER, rank
    if code in ("220", "230", "030"):
        return EVENT_CATEGORY_CAPITAL_STRUCTURE, DOC_KIND_OTHER, rank
    if code in ("350", "360"):
        return EVENT_CATEGORY_SHAREHOLDING, DOC_KIND_OTHER, rank
    if code in ("135", "136", "235", "236"):
        return EVENT_CATEGORY_GOVERNANCE, DOC_KIND_OTHER, rank
    return EVENT_CATEGORY_OTHER, DOC_KIND_OTHER, rank


def filing_headline(filing: Any) -> str:
    """An English headline from the listing's own METADATA: type, fiscal period and
    EDINET's Japanese description. A fiscal year is named by the year it ENDS in
    (``document_period``'s convention): H1 of the year to 31 March 2027 is "H1 FY2027"."""
    code = getattr(filing, "doc_type_code", None) or ""
    label = _DOC_TYPES.get(code, ("EDINET filing", RANK_ORDINARY))[0]
    description = (getattr(filing, "doc_description", None) or "").strip()
    end: date | None = getattr(filing, "period_end", None)
    start: date | None = getattr(filing, "period_start", None)
    if code == DOC_ANNUAL and end:
        head = f"{label} for the fiscal year ended {_long_date(end)}"
    elif code == DOC_HALF_YEAR and end and start:
        head = f"H1 FY{end.year} {label} (six months from {_long_date(start)})"
    elif code == DOC_QUARTERLY and end:
        quarter = _QUARTER_RE.search(description)
        ordinal = quarter.group(1).translate(_FULLWIDTH_DIGITS) if quarter else None
        head = f"Q{ordinal} FY{end.year} {label}" if ordinal else f"{label}, FY{end.year}"
    else:
        head = label
    return f"{head} — {description}"[:300] if description else head[:300]


def to_official_document(filing: Any) -> OfficialDocument:
    category, doc_kind, rank = classify_filing(filing.doc_type_code)
    url = PDF_URL.format(doc_id=filing.doc_id)
    return OfficialDocument(
        source_id=SOURCE_EDINET, document_ref=filing.doc_id,
        published_at=filing.submitted_at, headline=filing_headline(filing),
        venue_category=filing.doc_description or "", category=category, doc_kind=doc_kind,
        research_rank=rank, price_sensitive=None, media="pdf", official_url=url,
        content_url=url,
    )


async def resolve_edinet_issuer(
    company: Any, *, cfg: Any, getter: Getter | None = None,
) -> tuple[VerifiedIssuer | None, str | None, str | None]:
    """``(issuer, edinet_code, why-not)``. ``why`` None with no issuer means the code
    list could not be read (a source problem, not a verdict about the issuer)."""
    rows = await load_code_list(cfg, getter=getter)
    if not rows:
        return None, None, None
    row, why = match_issuer(rows, ticker=getattr(company, "ticker", None),
                            name=getattr(company, "name", None))
    if row is None:
        return None, None, why
    return VerifiedIssuer(
        company_id=company.id, ticker=row.tse_code, venue=VENUE_TSE,
        name=row.name_en or row.name_ja, source_id=SOURCE_EDINET,
        identity_basis=f"EDINET code list: {row.edinet_code}, securities code "
                       f"{row.sec_code} ({row.name_en or row.name_ja})",
        local_name=row.name_ja or None, source_ref=row.edinet_code,
    ), row.edinet_code, None


async def list_edinet_disclosures(
    session: Any, company: Any, *, cfg: Any, getter: Getter | None = None,
    now: datetime | None = None, index_pause: float = 0.3,
) -> DisclosureListing:
    """One issuer's EDINET filings in the window, or why there are none. Never raises."""
    import sqlalchemy as sa

    from app.models.edinet_filing import EdinetFiling

    if not is_enabled(cfg):
        return DisclosureListing(issuer=None, reason=REASON_CONNECTOR_DISABLED,
                                 detail="EDINET is off or EDINET_API_KEY is not set")
    try:
        issuer, edinet_code, why = await resolve_edinet_issuer(company, cfg=cfg,
                                                               getter=getter)
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=None, reason=REASON_IDENTITY_UNVERIFIED,
                                 detail=type(exc).__name__, requests=1)
    if issuer is None or edinet_code is None:
        return DisclosureListing(
            issuer=None,
            reason=REASON_IDENTITY_UNVERIFIED if why else REASON_SOURCE_UNAVAILABLE,
            detail=why or "the EDINET code list could not be read", requests=1)
    now = now or datetime.now(timezone.utc)
    lookback = max(1, int(getattr(cfg, "v3_disclosure_lookback_days", 560) or 560))
    try:
        refresh = await refresh_index(
            session, cfg=cfg, now=now, lookback_days=lookback, getter=getter,
            max_days=int(getattr(cfg, "v3_edinet_index_days_per_run", 40) or 0),
            pause=index_pause)
        days = window_days(now, lookback)
        held = await _days_held(session, days)
        cutoff = now - timedelta(days=lookback)
        result = await session.execute(
            sa.select(EdinetFiling)
            .where(EdinetFiling.edinet_code == edinet_code,
                   EdinetFiling.submitted_at >= cutoff)
            .order_by(EdinetFiling.submitted_at.desc()))
        filings = list(result.scalars())
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail=type(exc).__name__, requests=1)
    requests = 1 + refresh.days_read + refresh.days_failed
    holes = sum(1 for d in days if d not in held)
    if not held:
        return DisclosureListing(
            issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE, requests=requests,
            pages_failed=holes,
            detail="no EDINET day listing could be read"
                   + (f" ({refresh.errors[0]})" if refresh.errors else ""))
    documents: list[OfficialDocument] = []
    refused = 0
    for filing in filings:
        # Withdrawn filings and filings with no PDF are not the record.
        if (filing.withdrawal_status or "0") != "0" or not filing.pdf_flag:
            refused += 1
            continue
        documents.append(to_official_document(filing))
    return DisclosureListing(
        issuer=issuer, documents=documents, requests=requests, refused=refused,
        pages_failed=holes,
        reason=None if documents else REASON_DATA_NOT_SOURCED,
        detail=(f"{holes} day(s) of the {len(days)}-day window not yet read from EDINET"
                if holes else None),
    )


__all__ = [
    "ATTRIBUTION",
    "CODE_LIST_URL",
    "DOCUMENT_LIST_URL",
    "EDINET_API_HOST",
    "EDINET_DL_HOST",
    "KEY_HEADER",
    "PDF_URL",
    "EdinetCodeRow",
    "IndexRefresh",
    "api_key",
    "classify_filing",
    "filing_headline",
    "guarded_get",
    "is_enabled",
    "list_edinet_disclosures",
    "load_code_list",
    "match_issuer",
    "parse_code_list_csv",
    "parse_code_list_zip",
    "parse_document_list",
    "refresh_index",
    "resolve_edinet_issuer",
    "to_official_document",
    "window_days",
]
