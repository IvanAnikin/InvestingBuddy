"""OpenDART (Korea FSS) — the issuer's own periodic and material filings.

V3.20 slice 11.3. DART is the Financial Supervisory Service's electronic disclosure
system: every KOSPI and KOSDAQ company files its annual business report (사업보고서),
half-year report (반기보고서), quarterly reports (분기보고서) and major-matters reports
(주요사항보고서) there. OpenDART is its official API.

IDENTITY
========
OpenDART's corp-code list (``corpCode.xml``) states for every filer its eight-digit
``corp_code``, Korean and English names and, when listed, its six-character stock code.
A company is matched on its KRX code (``005930``, ``005930.KS``) AND a name that
agrees: the English name by the ``find_listing`` rule, or the Korean name exactly. A
ticker alone never decides. The list is one slow download (3.6 MB at ~7 KB/s, 499 s
measured), so its listed rows are HELD (``opendart_corp_codes``, migration 060),
refreshed by ``scripts/opendart_corp_codes_refresh.py`` and downloaded inside a run only
when nothing fresh is held. A fetched document must then carry the same ``corp_code`` in
its own header (``<COMPANY-NAME AREGCIK="…">``) and name the issuer
(``acquisition.document_names_issuer``) before anything is stored.

LISTING
=======
``list.json`` is per company (unlike EDINET): periodic reports (``pblntf_ty=A``),
major-matters reports (``B``) and exchange disclosures (``I``) in the lookback window,
each type read separately so the type is known from the query, never guessed from a
title. A row filed under another ``corp_code`` is refused.

CONTENT
=======
``document.xml?rcept_no=…`` — the filing's ORIGINAL document, a zip holding DART's own
XML markup (``dart4.xsd``: ``TABLE``/``TR``/``TD``/``TE``/``P``/``TITLE``). The main
document (``<rcept_no>.xml``) is converted to plain HTML and read by the same extractor
as every other primary document. The public viewer page
(``dart.fss.or.kr/dsaf001/main.do?rcpNo=…``) is what a citation opens. DART's PDF
download needs a browser session and is not used.

THE KEY
=======
OpenDART accepts its key ONLY as the ``crtfc_key`` query parameter. So it is passed to
the transport as a parameter (``transport.guarded_get``), never written into a URL this
module builds, stores, logs or returns; the transport reports failures as codes, never
as exception text (which can carry the request URL).

TERMS: OpenDART data is public disclosure information published by the FSS; the API is
free with a daily request allowance (20,000 per key). Cite "DART (금융감독원)" as the source.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    SOURCE_OPENDART,
    VENUE_KRX,
    DisclosureListing,
    OfficialDocument,
    VerifiedIssuer,
)
from app.services.sources.disclosures.transport import guarded_get

logger = logging.getLogger(__name__)

OPENDART_HOST = "opendart.fss.or.kr"
DART_VIEWER_HOST = "dart.fss.or.kr"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
KEY_PARAM = "crtfc_key"
ATTRIBUTION = "Source: DART, Financial Supervisory Service of Korea (금융감독원)"

_KST = ZoneInfo("Asia/Seoul")
_RCEPT_NO_RE = re.compile(r"^[0-9]{14}$")
_CORP_CODE_RE = re.compile(r"^[0-9]{8}$")
#: A KRX code: six characters, digits (and, for some newer listings, a letter).
_KRX_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{5}$")

MAX_CORP_CODE_ZIP_BYTES = 30_000_000
MAX_CORP_CODE_XML_BYTES = 80_000_000
MAX_LIST_BYTES = 2_000_000
MAX_DOCUMENT_ZIP_BYTES = 40_000_000
#: A main document, decompressed (a large annual report's XML measured ~15 MB).
MAX_DOCUMENT_XML_BYTES = 60_000_000
_CORP_CODE_TTL_SECONDS = 24 * 3600
#: Listing pages read per disclosure type (100 rows each).
MAX_PAGES_PER_TYPE = 3

#: OpenDART statuses: "000" normal, "013" no data. Anything else is a failure
#: ("010" unregistered key, "011" key not allowed, "020" request limit exceeded…).
_STATUS_OK = "000"
_STATUS_NO_DATA = "013"
_STATUS_KEY_REFUSED = frozenset({"010", "011", "012", "901"})

#: Disclosure types read for research: periodic, major matters, exchange disclosures.
LISTED_TYPES = ("A", "B", "I")

Getter = Callable[..., Awaitable[tuple[int | None, bytes | None, str | None]]]

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_PERIOD_RE = re.compile(r"\((\d{4})\.(\d{2})\)")
_AMENDED_RE = re.compile(
    r"^\s*\[(?:기재정정|첨부정정|첨부추가|변경등록|연장결정|발행조건확정|정정명령부과|정정제출요구)\]")


def api_key(cfg: Any) -> str:
    return str(getattr(cfg, "opendart_api_key", "") or "").strip()


def is_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_opendart_disclosures_enabled", False)) and bool(api_key(cfg))


def _status(payload: bytes | None) -> tuple[str | None, dict[str, Any] | None]:
    try:
        data = json.loads((payload or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return str(data.get("status") or ""), data


# ── The corp-code list (identity) ──────────────────────────────────────────── #


@dataclass(frozen=True)
class CorpCodeRow:
    corp_code: str
    name_ko: str
    name_en: str
    stock_code: str


def _field(block: str, tag: str) -> str:
    start = block.find(f"<{tag}>")
    if start < 0:
        return ""
    start += len(tag) + 2
    end = block.find(f"</{tag}>", start)
    return html.unescape(block[start:end]).strip() if end >= 0 else ""


def parse_corp_code_xml(text: str) -> list[CorpCodeRow]:
    """LISTED rows of ``CORPCODE.xml`` (a stock code present). One linear pass with
    ``str.find``; never raises."""
    rows: list[CorpCodeRow] = []
    pos = 0
    body = text or ""
    while True:
        start = body.find("<list>", pos)
        if start < 0:
            break
        end = body.find("</list>", start)
        if end < 0:
            break
        block = body[start + 6:end]
        pos = end + 7
        if len(block) > 2_000:
            continue
        stock = _field(block, "stock_code").upper()
        code = _field(block, "corp_code")
        if not stock or not _KRX_CODE_RE.match(stock) or not _CORP_CODE_RE.match(code):
            continue
        rows.append(CorpCodeRow(corp_code=code, name_ko=_field(block, "corp_name")[:300],
                                name_en=_field(block, "corp_eng_name")[:300],
                                stock_code=stock))
    return rows


def _one_member(payload: bytes, *, max_bytes: int, name: str | None = None) -> bytes | None:
    """One member of a zip, size-capped on its declared AND actual size, or None."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if name is not None:
                members = [m for m in members if m.filename == name]
            if len(members) != 1 or members[0].file_size > max_bytes:
                return None
            with archive.open(members[0]) as handle:
                raw = handle.read(max_bytes + 1)
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return None
    return raw if len(raw) <= max_bytes else None


def parse_corp_code_zip(payload: bytes) -> list[CorpCodeRow]:
    raw = _one_member(payload, max_bytes=MAX_CORP_CODE_XML_BYTES)
    return parse_corp_code_xml(raw.decode("utf-8", "replace")) if raw else []


_CORP_CODE_CACHE: dict[str, tuple[float, list[CorpCodeRow]]] = {}


def fetch_timeout(cfg: Any) -> float:
    """The deadline of one OpenDART request (the host was measured at ~7 KB/s)."""
    return float(getattr(cfg, "v3_opendart_fetch_timeout_seconds", 240) or 240)


async def download_corp_codes(cfg: Any, *, getter: Getter | None = None,
                              timeout_seconds: float | None = None) -> list[CorpCodeRow]:
    """Listed companies from a fresh download of the corp-code list (slow: measured
    499 s), cached for a day per process. ``[]`` when it cannot be read."""
    cached = _CORP_CODE_CACHE.get("rows")
    if cached and time.monotonic() - cached[0] < _CORP_CODE_TTL_SECONDS:
        return cached[1]
    _status_code, body, _error = await (getter or guarded_get)(
        CORP_CODE_URL, params={KEY_PARAM: api_key(cfg)}, cfg=cfg,
        max_bytes=MAX_CORP_CODE_ZIP_BYTES, allowed_host=OPENDART_HOST,
        timeout_seconds=timeout_seconds or fetch_timeout(cfg))
    if not body or not body.startswith(b"PK"):
        return []
    rows = await asyncio.to_thread(parse_corp_code_zip, body)
    if rows:
        _CORP_CODE_CACHE["rows"] = (time.monotonic(), rows)
    return rows


async def store_corp_codes(session: Any, rows: list[CorpCodeRow], *, now: datetime) -> int:
    """Replace the held list with ``rows`` (one read of the whole list). A delisted
    company leaves the list, so the held rows are replaced, never merged."""
    import sqlalchemy as sa

    from app.models.opendart_corp_code import OpendartCorpCode

    unique = {r.corp_code: r for r in rows if r.corp_code and r.stock_code}
    if not unique:
        return 0
    await session.execute(sa.delete(OpendartCorpCode))
    session.add_all([
        OpendartCorpCode(corp_code=r.corp_code, stock_code=r.stock_code,
                         name_ko=(r.name_ko or "")[:300],
                         name_en=(r.name_en or "")[:300] or None, refreshed_at=now)
        for r in unique.values()])
    await session.flush()
    return len(unique)


async def held_corp_codes(session: Any, stock_code: str
                          ) -> tuple[list[CorpCodeRow], datetime | None]:
    """The held rows for one KRX code, and when the held list was read (``None`` when
    nothing is held)."""
    import sqlalchemy as sa

    from app.models.opendart_corp_code import OpendartCorpCode

    refreshed = (await session.execute(
        sa.select(sa.func.max(OpendartCorpCode.refreshed_at)))).scalar_one_or_none()
    if refreshed is None:
        return [], None
    if refreshed.tzinfo is None:  # SQLite returns naive timestamps
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    held = (await session.execute(sa.select(OpendartCorpCode).where(
        OpendartCorpCode.stock_code == stock_code))).scalars().all()
    return [CorpCodeRow(corp_code=h.corp_code, name_ko=h.name_ko, name_en=h.name_en or "",
                        stock_code=h.stock_code) for h in held], refreshed


async def corp_code_rows(session: Any, code: str, *, cfg: Any, getter: Getter | None,
                         now: datetime) -> list[CorpCodeRow] | None:
    """The listed rows for one KRX code: the held list while it is fresh; otherwise a
    download, which is then held. A stale held list still answers when the download
    fails. ``None``: nothing held and nothing downloaded (a source problem)."""
    held, refreshed = await held_corp_codes(session, code)
    max_age = timedelta(days=max(1, int(
        getattr(cfg, "v3_opendart_corp_codes_max_age_days", 30) or 30)))
    if refreshed is not None and now - refreshed <= max_age:
        return held
    rows = await download_corp_codes(cfg, getter=getter)
    if rows:
        try:
            async with session.begin_nested():
                await store_corp_codes(session, rows, now=now)
        except Exception as exc:  # noqa: BLE001 - not holding it costs speed, not truth
            logger.warning("opendart corp codes not stored: %s", type(exc).__name__)
        return [r for r in rows if r.stock_code == code]
    return held if refreshed is not None else None


def krx_code(ticker: str | None) -> str:
    """The six-character KRX code of ``005930`` / ``005930.KS`` / ``KRX:005930``, or ``""``."""
    from app.services.discovery.directories import _strip_suffix

    code = _strip_suffix(str(ticker or "")).upper()
    return code if _KRX_CODE_RE.match(code) else ""


def match_issuer(
    rows: list[CorpCodeRow], *, ticker: str | None, name: str | None,
) -> tuple[CorpCodeRow | None, str]:
    """``(row, "")`` for the listed company with this KRX code whose name agrees."""
    from app.services.discovery.directories import _acronym_agrees, _names_agree
    from app.services.discovery.identity import normalised_name

    code = krx_code(ticker)
    if not code:
        return None, "the ticker is not a KRX code"
    hits = [r for r in rows if r.stock_code == code]
    if not hits:
        return None, f"the OpenDART corp-code list has no listed company with code {code}"
    wanted = normalised_name(name)
    for row in hits:
        if (not wanted or (row.name_en and _names_agree(
                wanted, normalised_name(row.name_en), ticker_matched=True))
                or _acronym_agrees(row.name_en, name) or (name or "").strip() == row.name_ko):
            return row, ""
    return None, (f"OpenDART lists code {code} as {hits[0].name_en or hits[0].name_ko!r}, "
                  f"not {name!r}")


# ── Classification and titles (from the listing's own metadata) ───────────── #


def classify_filing(pblntf_ty: str, report_nm: str) -> tuple[str, str, int, str]:
    """``(category, doc_kind, rank, English label)`` for one listed filing."""
    from app.services.sources.disclosure_events import (
        EVENT_CATEGORY_OTHER,
        EVENT_CATEGORY_RESULTS,
        EVENT_CATEGORY_TRANSACTION,
    )
    from app.services.sources.document_discovery import (
        DOC_KIND_ANNUAL_REPORT,
        DOC_KIND_INTERIM_REPORT,
        DOC_KIND_OTHER,
        DOC_KIND_RESULTS_RELEASE,
    )

    name = report_nm or ""
    amended = bool(_AMENDED_RE.match(name))
    if pblntf_ty == "A":
        if "사업보고서" in name:
            label = "Annual Business Report"
            return (EVENT_CATEGORY_RESULTS, DOC_KIND_OTHER if amended else DOC_KIND_ANNUAL_REPORT,
                    RANK_PERIODIC if amended else RANK_ANNUAL,
                    ("Amended " if amended else "") + label)
        if "반기보고서" in name:
            label = "Half-year Report"
            return (EVENT_CATEGORY_RESULTS, DOC_KIND_OTHER if amended else DOC_KIND_INTERIM_REPORT,
                    RANK_ORDINARY if amended else RANK_INTERIM,
                    ("Amended " if amended else "") + label)
        if "분기보고서" in name:
            label = "Quarterly Report"
            return (EVENT_CATEGORY_RESULTS, DOC_KIND_OTHER if amended else DOC_KIND_INTERIM_REPORT,
                    RANK_ORDINARY if amended else RANK_PERIODIC,
                    ("Amended " if amended else "") + label)
        return EVENT_CATEGORY_RESULTS, DOC_KIND_OTHER, RANK_PERIODIC, "Periodic Filing"
    if pblntf_ty == "B":
        return (EVENT_CATEGORY_TRANSACTION, DOC_KIND_OTHER,
                RANK_ORDINARY if amended else RANK_MATERIAL, "Major Matters Report")
    # Exchange disclosures: a preliminary results statement (잠정실적) is a results
    # release; an IR notice or a share-related notice is administrative.
    if "실적" in name and "잠정" in name:
        return EVENT_CATEGORY_RESULTS, DOC_KIND_RESULTS_RELEASE, RANK_PERIODIC, \
            "Preliminary Results (exchange disclosure)"
    if any(w in name for w in ("기업설명회", "IR개최", "주주총회", "기준일", "명의개서")):
        return EVENT_CATEGORY_OTHER, DOC_KIND_OTHER, RANK_ADMINISTRATIVE, "Exchange Notice"
    return EVENT_CATEGORY_OTHER, DOC_KIND_OTHER, RANK_ORDINARY, "Exchange Disclosure"


def _fiscal_position(year: int, month: int, fy_end_month: int) -> tuple[int, int]:
    """``(fiscal year named by its END year, months into that fiscal year)``."""
    fiscal_year = year if month <= fy_end_month else year + 1
    months = (month - fy_end_month) % 12 or 12
    return fiscal_year, months


def filing_headline(pblntf_ty: str, report_nm: str, *, fy_end_month: int | None) -> str:
    """An English headline from the listing's metadata. The period in the report name
    ("(2026.09)") is the period END; with the issuer's fiscal year-end month known, a
    half-year or quarterly report is named on the fiscal calendar ("Q3 FY2026"), which is
    the convention ``document_period`` reads. Unknown year-end: no H/Q label at all."""
    label = classify_filing(pblntf_ty, report_nm)[3]
    found = _PERIOD_RE.search(report_nm or "")
    amended = bool(_AMENDED_RE.match(report_nm or ""))
    head = label
    if pblntf_ty == "A" and found and not amended:
        year, month = int(found.group(1)), int(found.group(2))
        if 1 <= month <= 12:
            end = f"{_MONTHS[month - 1]} {year}"
            if label == "Annual Business Report":
                head = f"{label} for the fiscal year ended {end}"
            elif fy_end_month and label in ("Half-year Report", "Quarterly Report"):
                fiscal_year, months = _fiscal_position(year, month, fy_end_month)
                if label == "Half-year Report" and months == 6:
                    head = f"H1 FY{fiscal_year} {label} (period to {end})"
                elif label == "Quarterly Report" and months in (3, 9):
                    head = f"Q{months // 3} FY{fiscal_year} {label} (period to {end})"
                else:
                    head = f"{label} (period to {end})"
            else:
                head = f"{label} (period to {end})"
    return f"{head} — {report_nm}"[:300] if report_nm else head[:300]


# ── The listing ────────────────────────────────────────────────────────────── #


def _published(rcept_dt: str) -> datetime | None:
    try:
        local = datetime.strptime(str(rcept_dt), "%Y%m%d")
    except (TypeError, ValueError):
        return None
    return local.replace(tzinfo=_KST).astimezone(timezone.utc)


def parse_list_page(payload: bytes | None, *, corp_code: str
                    ) -> tuple[list[dict[str, Any]] | None, int, int, str | None]:
    """``(rows, refused, total_pages, status)``; ``rows`` None when the page is not a
    usable answer ("no data" is an empty, usable answer). Pure; never raises."""
    status, data = _status(payload)
    if status == _STATUS_NO_DATA:
        return [], 0, 0, status
    if status != _STATUS_OK or data is None or not isinstance(data.get("list"), list):
        return None, 0, 0, status
    rows: list[dict[str, Any]] = []
    refused = 0
    for item in data["list"]:
        if not isinstance(item, dict):
            refused += 1
            continue
        rcept_no = str(item.get("rcept_no") or "")
        published = _published(str(item.get("rcept_dt") or ""))
        # A row filed under another company is never this issuer's filing.
        if (not _RCEPT_NO_RE.match(rcept_no) or published is None
                or str(item.get("corp_code") or "") != corp_code):
            refused += 1
            continue
        rows.append({"rcept_no": rcept_no, "published_at": published,
                     "report_nm": str(item.get("report_nm") or "").strip()[:300],
                     "filer": str(item.get("flr_nm") or "")[:200]})
    try:
        pages = int(data.get("total_page") or 1)
    except (TypeError, ValueError):
        pages = 1
    return rows, refused, pages, status


async def _fy_end_month(corp_code: str, *, cfg: Any, getter: Getter | None) -> int | None:
    _code, body, _error = await (getter or guarded_get)(
        COMPANY_URL, params={KEY_PARAM: api_key(cfg), "corp_code": corp_code}, cfg=cfg,
        max_bytes=200_000, allowed_host=OPENDART_HOST, timeout_seconds=fetch_timeout(cfg))
    status, data = _status(body)
    if status != _STATUS_OK or data is None:
        return None
    try:
        month = int(str(data.get("acc_mt") or "").strip())
    except ValueError:
        return None
    return month if 1 <= month <= 12 else None


async def resolve_opendart_issuer(
    session: Any, company: Any, *, cfg: Any, getter: Getter | None = None,
    now: datetime | None = None,
) -> tuple[VerifiedIssuer | None, str | None]:
    """``(issuer, why-not)``; ``why`` None with no issuer means the corp-code list could
    not be read (a source problem, not a verdict about the issuer)."""
    ticker, name = getattr(company, "ticker", None), getattr(company, "name", None)
    code = krx_code(ticker)
    if not code:
        return None, match_issuer([], ticker=ticker, name=name)[1]
    rows = await corp_code_rows(session, code, cfg=cfg, getter=getter,
                                now=now or datetime.now(timezone.utc))
    if rows is None:
        return None, None
    row, why = match_issuer(rows, ticker=ticker, name=name)
    if row is None:
        return None, why
    return VerifiedIssuer(
        company_id=company.id, ticker=row.stock_code, venue=VENUE_KRX,
        name=row.name_en or row.name_ko, source_id=SOURCE_OPENDART,
        identity_basis=f"OpenDART corp-code list: {row.corp_code}, stock code "
                       f"{row.stock_code} ({row.name_en or row.name_ko})",
        local_name=row.name_ko or None, source_ref=row.corp_code,
    ), None


async def list_opendart_disclosures(
    session: Any, company: Any, *, cfg: Any, getter: Getter | None = None,
    now: datetime | None = None,
) -> DisclosureListing:
    """One issuer's OpenDART filings in the window, or why there are none. Never raises."""
    if not is_enabled(cfg):
        return DisclosureListing(issuer=None, reason=REASON_CONNECTOR_DISABLED,
                                 detail="OpenDART is off or OPENDART_API_KEY is not set")
    try:
        issuer, why = await resolve_opendart_issuer(session, company, cfg=cfg,
                                                    getter=getter, now=now)
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=None, reason=REASON_IDENTITY_UNVERIFIED,
                                 detail=type(exc).__name__, requests=1)
    if issuer is None or not issuer.source_ref:
        return DisclosureListing(
            issuer=None,
            reason=REASON_IDENTITY_UNVERIFIED if why else REASON_SOURCE_UNAVAILABLE,
            detail=why or "the OpenDART corp-code list could not be read", requests=1)
    corp_code = issuer.source_ref
    now = now or datetime.now(timezone.utc)
    lookback = max(1, int(getattr(cfg, "v3_disclosure_lookback_days", 560) or 560))
    begin = (now.astimezone(_KST) - timedelta(days=lookback)).strftime("%Y%m%d")
    end = now.astimezone(_KST).strftime("%Y%m%d")
    requests = 1
    try:
        fy_end = await _fy_end_month(corp_code, cfg=cfg, getter=getter)
        requests += 1
        documents: list[OfficialDocument] = []
        refused = pages_failed = 0
        reached = False
        for pblntf_ty in LISTED_TYPES:
            page, pages = 1, 1
            while page <= min(pages, MAX_PAGES_PER_TYPE):
                status_code, body, _error = await (getter or guarded_get)(
                    LIST_URL, params={KEY_PARAM: api_key(cfg), "corp_code": corp_code,
                                      "bgn_de": begin, "end_de": end,
                                      "pblntf_ty": pblntf_ty, "page_no": str(page),
                                      "page_count": "100"},
                    cfg=cfg, max_bytes=MAX_LIST_BYTES, allowed_host=OPENDART_HOST,
                    timeout_seconds=fetch_timeout(cfg))
                requests += 1
                rows, bad, pages, status = parse_list_page(body, corp_code=corp_code)
                if rows is None:
                    pages_failed += 1
                    if status in _STATUS_KEY_REFUSED or status_code in (401, 403):
                        return DisclosureListing(
                            issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                            detail=f"OpenDART refused the key (status {status})",
                            requests=requests, pages_failed=pages_failed)
                    break
                reached = True
                refused += bad
                for row in rows:
                    category, doc_kind, rank, _label = classify_filing(
                        pblntf_ty, row["report_nm"])
                    url = VIEWER_URL.format(rcept_no=row["rcept_no"])
                    documents.append(OfficialDocument(
                        source_id=SOURCE_OPENDART, document_ref=row["rcept_no"],
                        published_at=row["published_at"],
                        headline=filing_headline(pblntf_ty, row["report_nm"],
                                                 fy_end_month=fy_end),
                        venue_category=row["report_nm"], category=category,
                        doc_kind=doc_kind, research_rank=rank, price_sensitive=None,
                        media="html", official_url=url, content_url=url))
                page += 1
    except Exception as exc:  # noqa: BLE001
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail=type(exc).__name__, requests=requests)
    if not reached:
        return DisclosureListing(issuer=issuer, reason=REASON_SOURCE_UNAVAILABLE,
                                 detail="no OpenDART listing page could be read",
                                 requests=requests, pages_failed=pages_failed)
    unique = {d.document_ref: d for d in documents}
    ordered = sorted(unique.values(), key=lambda d: d.published_at or now, reverse=True)
    return DisclosureListing(
        issuer=issuer, documents=ordered, requests=requests, refused=refused,
        pages_failed=pages_failed, reason=None if ordered else REASON_DATA_NOT_SOURCED)


# ── The document: DART XML → HTML → the shared extractor ───────────────────── #


class DartDocumentUnavailable(Exception):
    """The original document could not be fetched or read (a code, never a URL)."""


class DartIdentityMismatch(Exception):
    """The document's own header names another company."""


#: DART tags → HTML. Anything not listed keeps its text and loses its tag.
_TAG_MAP = {
    "DOCUMENT-NAME": "h1", "COVER-TITLE": "h1", "TITLE": "h2", "P": "p",
    "TABLE": "table", "THEAD": "thead", "TBODY": "tbody", "TR": "tr", "TD": "td",
    "TH": "th", "TE": "td", "TU": "td", "SPAN": "span", "COMPANY-NAME": "p",
}
_DROP_BLOCKS = ("SUMMARY", "IMAGE", "IMG-CAPTION", "COLGROUP")
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9\-]{0,40})((?:\s[^<>]{0,2000})?)(/?)>")
_SPAN_ATTR_RE = re.compile(r'\b(COLSPAN|ROWSPAN)="(\d{1,3})"', re.I)


def dart_xml_to_html(xml_text: str) -> str:
    """DART's ``dart4.xsd`` markup as plain HTML: tables stay tables, section titles
    become headings, everything else keeps its text. One linear regex pass. Pure."""
    body = xml_text or ""
    for block in _DROP_BLOCKS:
        body = re.sub(rf"<{block}\b[^>]*>.*?</{block}>", " ", body, flags=re.S)
    body = re.sub(r"<\?xml[^>]*\?>", "", body)
    body = body.replace("&cr;", "<br/>")

    def tag(m: re.Match[str]) -> str:
        closing, name, attrs, selfclose = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        if name == "PGBRK":
            return "<hr/>"
        if name == "BR":  # DART's ``&cr;`` line break, turned into <br/> above
            return "<br/>"
        if name in ("IMG", "COL"):
            return ""
        mapped = _TAG_MAP.get(name)
        if mapped is None:
            return " " if not closing else "\n"
        if closing:
            return f"</{mapped}>"
        spans = "".join(f' {a.lower()}="{v}"' for a, v in _SPAN_ATTR_RE.findall(attrs or ""))
        return f"<{mapped}{spans}{'/' if selfclose else ''}>"

    converted = _TAG_RE.sub(tag, body)
    return ("<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"></head><body>"
            + converted + "</body></html>")


_AREGCIK_RE = re.compile(r'<COMPANY-NAME\b[^>]{0,200}\bAREGCIK="([0-9]{8})"')


def main_document_xml(payload: bytes, *, rcept_no: str) -> str:
    """The filing's main document from the ``document.xml`` zip, decoded."""
    raw = _one_member(payload, max_bytes=MAX_DOCUMENT_XML_BYTES, name=f"{rcept_no}.xml")
    if raw is None:
        raise DartDocumentUnavailable("main_document_missing")
    head = raw[:300].decode("ascii", "replace")
    found = re.search(r'encoding="([A-Za-z0-9_\-]{1,20})"', head)
    encoding = found.group(1) if found else "utf-8"
    try:
        return raw.decode(encoding, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def document_extractor(document: OfficialDocument, issuer: VerifiedIssuer, *,
                       getter: Getter | None = None) -> Callable[..., Awaitable[Any]]:
    """An extractor with ``live_primary_document_extractor``'s signature that reads
    THIS filing's original document through the API instead of fetching ``url``."""

    async def _extract(url: str, *, allowed_domains: tuple[str, ...],
                       title_hint: str | None = None, issuer_context: Any = None,
                       cfg: Any = None, period_policy: str | None = None,
                       published_at: date | None = None, **_: Any) -> Any:
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        started = time.perf_counter()
        _code, body, error = await (getter or guarded_get)(
            DOCUMENT_URL, params={KEY_PARAM: api_key(cfg),
                                  "rcept_no": document.document_ref},
            cfg=cfg, max_bytes=MAX_DOCUMENT_ZIP_BYTES, allowed_host=OPENDART_HOST,
            timeout_seconds=fetch_timeout(cfg))
        if not body or not body.startswith(b"PK"):
            status, _data = _status(body)
            raise DartDocumentUnavailable(error or f"status_{status or 'unreadable'}")
        xml_text = await asyncio.to_thread(main_document_xml, body,
                                           rcept_no=document.document_ref)
        stated = _AREGCIK_RE.search(xml_text[:5000])
        if not stated or stated.group(1) != issuer.source_ref:
            raise DartIdentityMismatch("corp_code_differs")
        page = await asyncio.to_thread(dart_xml_to_html, xml_text)
        fetched = DocumentFetchResult(
            requested_url=document.official_url, final_url=document.official_url,
            status_code=200, content_type="text/html", document_type="html",
            content=page.encode("utf-8"))
        return await live_fetchers._artifact_from_fetch(
            fetched, title=title_hint, original_language="ko",
            issuer_context=issuer_context, cfg=cfg,
            fetch_ms=int((time.perf_counter() - started) * 1000),
            period_policy=period_policy, published_at=published_at)

    return _extract


__all__ = [
    "ATTRIBUTION",
    "CORP_CODE_URL",
    "CorpCodeRow",
    "DART_VIEWER_HOST",
    "DOCUMENT_URL",
    "DartDocumentUnavailable",
    "DartIdentityMismatch",
    "LIST_URL",
    "OPENDART_HOST",
    "VIEWER_URL",
    "api_key",
    "classify_filing",
    "corp_code_rows",
    "dart_xml_to_html",
    "document_extractor",
    "download_corp_codes",
    "fetch_timeout",
    "filing_headline",
    "held_corp_codes",
    "is_enabled",
    "krx_code",
    "list_opendart_disclosures",
    "main_document_xml",
    "match_issuer",
    "parse_corp_code_xml",
    "parse_corp_code_zip",
    "parse_list_page",
    "resolve_opendart_issuer",
    "store_corp_codes",
]
