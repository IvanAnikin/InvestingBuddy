"""Official exchange issuer directories — the listing backbone of discovery. V3.19.10.

WHY
===
V3.19.4 verified a lead's listing on a page the external search provider had cited. On
2026-09-26 that provider's builtin web search stopped issuing searches (every model, every
prompt: the tool is accepted and never called — measured, see the V3.19 acceptance
report), so leads arrived with no URL at all and NOTHING could be verified: the first live
European-luxury run screened eight curated names and could verify none of them.

The spec already ranks the sources: **official exchange directories first**. Exchanges
publish their own lists of what trades on them, free and machine-readable. A lead whose
(venue, ticker) — or ISIN, or exact name on that venue — appears in the exchange's own
directory IS a verified listing, by the most authoritative source there is for that fact,
with no vendor in the loop.

Some directories also publish what the SIZE constraint needs:

* **ASX** — market capitalisation (AUD), per company;
* **Euronext** — the official closing price, and on each instrument's factsheet the
  number of ADMITTED SHARES: shares × price is an exchange-sourced market cap.

* **TWSE** (V3.20 slice 11.1) — the ISSUED SHARES of every listed company in its open-data
  company file, and the official closing price in its daily file: shares × close.

Directories covered: Euronext (Paris, Milan, Amsterdam, Brussels, Lisbon, Dublin, Oslo
and their growth markets), SIX Swiss Exchange, ASX, TSX, TSX Venture, the Taiwan Stock
Exchange (open data, Open Government Data License v1.0, attribution), and the US
exchanges through the SEC's own ticker-exchange file. Each is fetched through the
platform's guarded fetcher (its own host only), parsed defensively, and cached per process
per day. A directory that cannot be read is a missing source, never a negative answer.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.services.discovery.identity import normalised_name

TIER_EXCHANGE = "exchange"
TIER_REGULATOR = "regulator"


@dataclass(frozen=True)
class DirectoryListing:
    name: str
    ticker: str
    exchange: str  # registry venue code
    isin: str | None
    country: str | None
    market_cap: float | None
    currency: str | None
    price: float | None
    industry: str | None
    mic: str | None
    directory: str
    source_url: str
    tier: str
    as_of: str
    #: SEC Central Index Key — only for the SEC directory.
    cik: str | None = None
    #: Issued shares, when the directory publishes them (TWSE).
    shares: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Directory:
    key: str
    host: str
    url: str
    tier: str
    label: str
    #: The Settings flag that switches this directory on (None: always on).
    flag: str | None = None


EURONEXT = _Directory(
    "euronext", "live.euronext.com",
    "https://live.euronext.com/en/pd_es/data/stocks/download?mics=dm_all_stock"
    "&initialLetter=&fe_type=csv&fe_decimal_separator=.&fe_date_format=d%2Fm%2FY",
    TIER_EXCHANGE, "Euronext official equities list",
)
ASX = _Directory(
    "asx", "asx.api.markitdigital.com",
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file",
    TIER_EXCHANGE, "ASX official listed companies directory",
)
TSX = _Directory(
    "tsx", "www.tsx.com", "https://www.tsx.com/json/company-directory/search/tsx/%5E*",
    TIER_EXCHANGE, "TSX official company directory",
)
TSXV = _Directory(
    "tsxv", "www.tsx.com", "https://www.tsx.com/json/company-directory/search/tsxv/%5E*",
    TIER_EXCHANGE, "TSX Venture official company directory",
)
SIX = _Directory(
    "six", "www.six-group.com",
    "https://www.six-group.com/sheldon/equity_issuers/v1/equity_issuers.csv",
    TIER_EXCHANGE, "SIX Swiss Exchange official equity issuers list",
)
SEC = _Directory(
    "sec", "www.sec.gov", "https://www.sec.gov/files/company_tickers_exchange.json",
    TIER_REGULATOR, "SEC company tickers and exchanges",
)
#: V3.20 slice 11.1 — Taiwan Stock Exchange open data (data.gov.tw, Open Government Data
#: License v1.0: any purpose, attribution required). The company file carries the issued
#: shares and an English short name; the daily file the official closing price.
TWSE = _Directory(
    "twse", "openapi.twse.com.tw", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    TIER_EXCHANGE, "TWSE listed company basic information (open data)",
    flag="v3_directory_twse_enabled",
)
TWSE_PRICES = _Directory(
    "twse_prices", "openapi.twse.com.tw",
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    TIER_EXCHANGE, "TWSE daily closing prices (open data)", flag="v3_directory_twse_enabled",
)
TWSE_ATTRIBUTION = "Source: Taiwan Stock Exchange open data (Open Government Data License v1.0)"
#: ADR-120 — the London Stock Exchange's own monthly "Instrument list" (every equity
#: instrument: TIDM, issuer, ISIN, market). It replaced ``api.londonstockexchange.com``,
#: whose host stopped resolving in 2026-11 and took every LSE listing check with it. It
#: carries no market capitalisation: an LSE size is verified on a fetched page only.
LSE = _Directory(
    "lse", "docs.londonstockexchange.com",
    "https://docs.londonstockexchange.com/reports/instruments/Instrument%20list.xlsx",
    TIER_EXCHANGE, "London Stock Exchange official instrument list",
)
DIRECTORIES: tuple[_Directory, ...] = (EURONEXT, ASX, TSX, TSXV, SIX, SEC, TWSE, LSE)

#: Euronext market name → (registry venue code, MIC for the instrument factsheet).
EURONEXT_MARKETS: dict[str, tuple[str, str]] = {
    "euronext paris": ("PA", "XPAR"), "euronext growth paris": ("PA", "ALXP"),
    "euronext access paris": ("PA", "XMLI"), "euronext milan": ("MI", "MTAA"),
    "euronext growth milan": ("MI", "EXGM"), "euronext amsterdam": ("AS", "XAMS"),
    "euronext brussels": ("BR", "XBRU"), "euronext growth brussels": ("BR", "ALXB"),
    "euronext lisbon": ("LS", "XLIS"), "euronext dublin": ("IR", "XMSM"),
    "euronext growth dublin": ("IR", "XESM"), "oslo børs": ("OL", "XOSL"),
    "euronext growth oslo": ("OL", "MERK"), "euronext expand oslo": ("OL", "XOAS"),
}
#: Venue code → the directory that is authoritative for it.
DIRECTORY_FOR_VENUE: dict[str, _Directory] = {
    "PA": EURONEXT, "MI": EURONEXT, "AS": EURONEXT, "BR": EURONEXT, "LS": EURONEXT,
    "IR": EURONEXT, "OL": EURONEXT, "AU": ASX, "TO": TSX, "V": TSXV, "SW": SIX, "US": SEC,
    "TW": TWSE, "LSE": LSE,
}


def directory_enabled(directory: _Directory, cfg: Any = None) -> bool:
    """Whether ``directory``'s flag is on. No ``cfg``: the application settings."""
    if directory.flag is None:
        return True
    if cfg is None:
        from app.core.config import settings as cfg
    return bool(getattr(cfg, directory.flag, False))


def directory_for_venue(venue: str | None, cfg: Any = None) -> _Directory | None:
    """The directory authoritative for ``venue`` when it is switched on, else None."""
    directory = DIRECTORY_FOR_VENUE.get(venue or "")
    return directory if directory is not None and directory_enabled(directory, cfg) else None

_CACHE: dict[tuple[str, str], list[DirectoryListing]] = {}
#: Per-key locks so concurrent leads download a directory once, not once each.
#: Keyed by the running loop too: an asyncio.Lock binds to the first loop that waits on it.
_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
#: directory key → monotonic time of its last failed download; retried after this long.
_FAILED_AT: dict[str, float] = {}
FAILURE_RETRY_SECONDS = 900.0
#: At most this many per-ticker LSE records are kept (the tickers come from model output).
MAX_LSE_ENTRIES = 500
#: A TIDM / ticker as it may appear in a URL path. Anything else is never requested.
_SAFE_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def reset_cache() -> None:
    """Forget every cached directory and failure (tests; a manual refresh)."""
    _CACHE.clear()
    _DOMICILE_CACHE.clear()
    _ANNUAL_ACCESSIONS_CACHE.clear()
    _ACCESSIONS_FAILED_AT.clear()
    _FAILED_AT.clear()
    _LOCKS.clear()


def _store(key: tuple[str, str], rows: list[DirectoryListing]) -> None:
    """Cache for today only: earlier days' copies are dropped, LSE records are capped."""
    today = _today()
    for stale in [k for k in _CACHE if k[1] != today]:
        del _CACHE[stale]
    if key[0].startswith("lse:"):
        lse = [k for k in _CACHE if k[0].startswith("lse:")]
        for old in lse[: max(0, len(lse) - MAX_LSE_ENTRIES + 1)]:
            del _CACHE[old]
    _CACHE[key] = rows


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _num(text: Any) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_euronext(text: str, source: _Directory = EURONEXT) -> list[DirectoryListing]:
    out = []
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        if len(row) < 14 or not row[1] or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", row[1]):
            continue
        market = row[3].strip().lower()
        venue = EURONEXT_MARKETS.get(market)
        if venue is None:
            continue  # Global Equity Market, EuroTLX, after-hours: not a primary listing
        out.append(DirectoryListing(
            name=row[0].strip(), ticker=row[2].strip().upper(), exchange=venue[0],
            isin=row[1], country=None, market_cap=None, currency=row[4].strip() or None,
            price=_num(row[13]) or _num(row[8]), industry=None, mic=venue[1],
            directory=source.key, source_url=source.url, tier=source.tier, as_of=_today(),
        ))
    return out


def parse_asx(text: str, source: _Directory = ASX) -> list[DirectoryListing]:
    out = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 5 or row[0] == "ASX code":
            continue
        out.append(DirectoryListing(
            name=row[1].strip(), ticker=row[0].strip().upper(), exchange="AU", isin=None,
            country="Australia", market_cap=_num(row[4]), currency="AUD", price=None,
            industry=row[2].strip() or None, mic="XASX", directory=source.key,
            source_url=source.url, tier=source.tier, as_of=_today(),
        ))
    return out


def parse_tmx(text: str, source: _Directory, venue: str) -> list[DirectoryListing]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for item in data.get("results") or []:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        out.append(DirectoryListing(
            name=str(item.get("name") or "").strip(), ticker=str(item["symbol"]).upper(),
            exchange=venue, isin=None, country="Canada", market_cap=None, currency="CAD",
            price=None, industry=None, mic="XTSE" if venue == "TO" else "XTSX",
            directory=source.key, source_url=source.url, tier=source.tier, as_of=_today(),
        ))
    return out


def parse_six(text: str, source: _Directory = SIX) -> list[DirectoryListing]:
    out = []
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        if len(row) < 7 or row[0] == "Company":
            continue
        out.append(DirectoryListing(
            name=row[0].strip(), ticker=row[2].strip().upper(), exchange="SW",
            isin=row[1].strip() or None, country=row[4].strip() or None, market_cap=None,
            currency=row[5].strip() or None, price=None, industry=None, mic="XSWX",
            directory=source.key, source_url=source.url, tier=source.tier, as_of=_today(),
        ))
    return out


def parse_sec(text: str, source: _Directory = SEC) -> list[DirectoryListing]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for cik, name, ticker, exchange in data.get("data") or []:
        if not ticker or str(exchange or "").upper() in ("", "OTC", "NONE"):
            continue
        out.append(DirectoryListing(
            name=str(name).strip(), ticker=str(ticker).upper(), exchange="US", isin=None,
            country=None, market_cap=None, currency="USD", price=None, industry=None,
            mic=str(exchange), directory=source.key, source_url=source.url,
            tier=source.tier, as_of=_today(), cik=str(cik) if cik else None,
        ))
    return out


def _roc_date(text: Any) -> str | None:
    """A Republic of China calendar date ("1151116" = 2026-11-16) as ISO, or None."""
    raw = str(text or "").strip()
    m = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


_TWSE_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")


def parse_twse(text: str, source: _Directory = TWSE) -> list[DirectoryListing]:
    """TWSE ``t187ap03_L``: one company per row, with its issued common shares."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("公司代號") or item.get("公司代碼") or "").strip().upper()
        if not _TWSE_CODE_RE.match(code):
            continue
        english = str(item.get("英文簡稱") or "").strip()
        local = str(item.get("公司簡稱") or item.get("公司名稱") or "").strip()
        foreign = str(item.get("外國企業註冊地國") or "").strip()
        out.append(DirectoryListing(
            name=english or local, ticker=code, exchange="TW", isin=None,
            country=None if foreign and foreign not in ("-", "－") else "Taiwan",
            market_cap=None, currency="TWD", price=None, industry=None, mic="XTAI",
            directory=source.key, source_url=source.url, tier=source.tier,
            as_of=_roc_date(item.get("出表日期")) or _today(),
            shares=_num(item.get("已發行普通股數或TDR原股發行股數")),
        ))
    return out


def parse_twse_prices(text: str, source: _Directory = TWSE_PRICES) -> list[DirectoryListing]:
    """TWSE ``STOCK_DAY_ALL``: the official closing price of every security that day."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code") or "").strip().upper()
        price = _num(item.get("ClosingPrice"))
        if not _TWSE_CODE_RE.match(code) or price is None:
            continue  # no trade that day: no closing price, never a zero
        out.append(DirectoryListing(
            name=str(item.get("Name") or "").strip(), ticker=code, exchange="TW", isin=None,
            country=None, market_cap=None, currency="TWD", price=price, industry=None,
            mic="XTAI", directory=source.key, source_url=source.url, tier=source.tier,
            as_of=_roc_date(item.get("Date")) or _today(),
        ))
    return out


_PARSERS = {
    "euronext": parse_euronext, "asx": parse_asx, "six": parse_six, "sec": parse_sec,
    "twse": parse_twse, "twse_prices": parse_twse_prices,
    "tsx": lambda text, source: parse_tmx(text, source, "TO"),
    "tsxv": lambda text, source: parse_tmx(text, source, "V"),
}


async def load(
    directory: _Directory, *, cfg: Any = None, fetcher: Any = None
) -> list[DirectoryListing]:
    """The directory's listings, fetched once per process per day. [] when unreadable,
    and [] without a request when the directory's flag is off."""
    if not directory_enabled(directory, cfg):
        return []
    key = (directory.key, _today())
    if key in _CACHE:
        return _CACHE[key]
    lock_key = (id(asyncio.get_running_loop()), directory.key)
    async with _LOCKS.setdefault(lock_key, asyncio.Lock()):
        if key in _CACHE:
            return _CACHE[key]
        failed = _FAILED_AT.get(directory.key)
        if failed is not None and time.monotonic() - failed < FAILURE_RETRY_SECONDS:
            return []
        listings = await _download(directory, cfg=cfg, fetcher=fetcher)
        if listings:
            _FAILED_AT.pop(directory.key, None)
            _store(key, listings)
        else:
            _FAILED_AT[directory.key] = time.monotonic()
        return listings


async def _download(
    directory: _Directory, *, cfg: Any = None, fetcher: Any = None
) -> list[DirectoryListing]:
    from app.services.sources.document_fetcher import safe_fetch_document

    binary = _BINARY_PARSERS.get(directory.key)
    try:
        result = await (fetcher or safe_fetch_document)(
            directory.url, allowed_domains=(directory.host,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=(_BINARY_CONTENT_TYPES if binary else (
                "text/csv", "application/csv", "application/json", "text/plain",
                "application/octet-stream")),
        )
    except Exception:  # noqa: BLE001 - an unreadable directory is a missing source
        return []
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        return []
    if binary is not None:
        try:
            return list(binary(content, directory))
        except Exception:  # noqa: BLE001 - a changed file shape is an unreadable directory
            return []
    # Not every exchange publishes UTF-8 (SIX's list is Latin-1: "Financière" → "Financi\ufffdre").
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", "replace")
    parser = _PARSERS[directory.key]
    try:
        return list(parser(text, directory))
    except Exception:  # noqa: BLE001 - a changed file shape is an unreadable directory
        return []


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
#: The instrument list's columns this parser reads (header row text → field).
_LSE_COLUMNS = {"tidm": "tidm", "issuer name": "issuer", "isin": "isin",
                "mifir identifier code": "mifir", "country of incorporation": "country",
                "trading currency": "currency", "lse market": "market"}
#: Equity instruments that are a company's shares: ordinary shares and depositary
#: receipts. Funds' units and other equity-like instruments are not a company listing.
_LSE_EQUITY_CODES = frozenset({"SHRS", "DPRS"})
_XLSX_CELL_RE = re.compile(r"^([A-Z]+)")


def _xlsx_rows(content: bytes, sheet: str = "xl/worksheets/sheet1.xml") -> list[list[str]]:
    """Every row of one worksheet as text cells, read with the standard library only.

    Shared strings are resolved; inline strings and numbers are read as text. Cells are
    placed by their column letter, so an empty cell never shifts the next one left.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(io.BytesIO(content)) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")).iter(_XLSX_NS + "si"):
                shared.append("".join(t.text or "" for t in item.iter(_XLSX_NS + "t")))
        rows: list[list[str]] = []
        with book.open(sheet) as handle:
            for _event, elem in ET.iterparse(handle):
                if elem.tag != _XLSX_NS + "row":
                    continue
                cells: dict[int, str] = {}
                for cell in elem.iter(_XLSX_NS + "c"):
                    ref = _XLSX_CELL_RE.match(cell.get("r") or "")
                    col = 0
                    for ch in (ref.group(1) if ref else ""):
                        col = col * 26 + (ord(ch) - 64)
                    value = cell.find(_XLSX_NS + "v")
                    if value is not None and value.text is not None:
                        text = (shared[int(value.text)] if cell.get("t") == "s"
                                else value.text)
                    else:
                        inline = cell.find(f"{_XLSX_NS}is/{_XLSX_NS}t")
                        text = inline.text if inline is not None and inline.text else ""
                    cells[max(col, len(cells) + 1)] = str(text).strip()
                if cells:
                    rows.append([cells.get(i, "") for i in range(1, max(cells) + 1)])
                elem.clear()
    return rows


def parse_lse_instruments(content: bytes, source: _Directory = LSE) -> list[DirectoryListing]:
    """The LSE instrument list's equity shares as listings (venue ``LSE``, MIC XLON)."""
    rows = _xlsx_rows(content)
    header_at = next((i for i, row in enumerate(rows)
                      if {"tidm", "isin"} <= {c.lower() for c in row}), None)
    if header_at is None:
        return []
    index = {_LSE_COLUMNS[c.lower()]: i for i, c in enumerate(rows[header_at])
             if c.lower() in _LSE_COLUMNS}
    if not {"tidm", "issuer", "isin"} <= set(index):
        return []
    out: list[DirectoryListing] = []
    seen: set[str] = set()

    def cell(row: list[str], key: str) -> str:
        i = index.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in rows[header_at + 1:]:
        tidm, issuer = cell(row, "tidm").upper(), cell(row, "issuer")
        mifir = cell(row, "mifir").upper()
        if not tidm or not issuer or tidm in seen or (mifir and mifir not in _LSE_EQUITY_CODES):
            continue
        seen.add(tidm)
        currency = cell(row, "currency").upper() or None
        out.append(DirectoryListing(
            name=issuer, ticker=tidm, exchange="LSE", isin=cell(row, "isin") or None,
            country=cell(row, "country") or None, market_cap=None,
            currency="GBP" if currency == "GBX" else currency, price=None,
            industry=None, mic="XLON", directory=source.key,
            source_url=source.url, tier=source.tier, as_of=_today(),
        ))
    return out


#: Directories published as a binary file, parsed from bytes rather than text.
_BINARY_PARSERS = {"lse": parse_lse_instruments}
#: Their content types, admitted for these downloads only.
_BINARY_CONTENT_TYPES = (
    "binary/octet-stream", "application/octet-stream",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


#: Venue suffixes a quoted ticker may carry ("KER.PA"). Anything else is PART of the
#: ticker — "BT.A" is BT Group's TIDM, not BT on venue "A".
_VENUE_SUFFIXES = frozenset({
    "PA", "MI", "AS", "BR", "LS", "IR", "OL", "AX", "AU", "TO", "V", "SW", "US", "L", "LN",
    "CO", "DE", "F", "HE", "ST", "MC", "VX", "CN", "NE", "TSX", "LSE", "ASX",
    "TW", "TWSE", "KS", "KQ", "NS", "BO", "T", "JP", "TYO",
})


def _strip_suffix(ticker: str) -> str:
    # "KER.PA", "MP.US", "EPA:KER" → "KER"; "BT.A" stays "BT.A"
    ticker = ticker.upper().split(":")[-1]
    head, dot, tail = ticker.rpartition(".")
    return head if dot and head and tail in _VENUE_SUFFIXES else ticker


async def find_listing(
    *, name: str | None, ticker: str | None, venue: str | None, isin: str | None = None,
    cfg: Any = None, fetcher: Any = None,
) -> tuple[DirectoryListing | None, str | None]:
    """``(listing, None)`` when the exchange's own directory lists this company, else
    ``(None, reason)``. ``reason`` is None when no directory covers the venue at all —
    which is "cannot check here", never "not listed".
    """
    directory = directory_for_venue(venue, cfg)
    if directory is None:
        return None, None
    listings = await load(directory, cfg=cfg, fetcher=fetcher)
    if not listings:
        return None, None  # the directory could not be read: no verdict either way
    in_venue = [row for row in listings if row.exchange == venue]
    symbol = _strip_suffix(ticker) if ticker else None
    wanted = normalised_name(name)
    if isin:
        hit = next((r for r in in_venue if r.isin == isin), None)
        if hit:
            return hit, None
    if symbol:
        # A share class is written "BRK.B" or "BRK-B" depending on the list.
        forms = {symbol, symbol.replace(".", "-"), symbol.replace("-", ".")}
        by_symbol = [r for r in in_venue if r.ticker in forms]
        # The symbol is authoritative only when the NAME agrees: a provider can pair a
        # real ticker with the wrong company.
        for row in by_symbol:
            if not wanted or _names_agree(wanted, normalised_name(row.name),
                                          ticker_matched=True):
                return row, None
            if _acronym_agrees(row.name, name):
                return row, None
        if by_symbol:
            return None, (
                f"{directory.label} lists {symbol} on {venue} as {by_symbol[0].name!r}, "
                f"not {name!r}"
            )
    if wanted:
        # With no ticker to anchor it, only the SAME name counts: "Kering Eyewear" is
        # not Kering, and must not inherit its listing and market cap.
        by_name = [r for r in in_venue if _expand(wanted) == _expand(normalised_name(r.name))]
        if len(by_name) == 1:
            return by_name[0], None
        if len(by_name) > 1:
            return None, f"{directory.label} lists several companies named like {name!r}"
    return None, f"not found in the {directory.label}"


#: Abbreviations exchange lists use ("HERMES INTL", "XYZ HLDGS").
_ABBREVIATIONS = {
    "intl": "international", "int": "international", "grp": "group", "hldgs": "holdings",
    "hldg": "holding", "hlds": "holdings", "corp": "corporation", "co": "company",
    "mfg": "manufacturing", "tech": "technologies", "techs": "technologies",
    "res": "resources", "min": "minerals", "ind": "industries", "inds": "industries",
    "&": "and", "svcs": "services", "sys": "systems",
}


def _expand(name: str) -> str:
    return " ".join(_ABBREVIATIONS.get(w, w) for w in name.split())


async def _lse_listing(
    *, name: str | None, ticker: str, cfg: Any = None, fetcher: Any = None
) -> tuple[DirectoryListing | None, str | None]:
    """The LSE instrument list's row for a TIDM (ADR-120). ``(None, None)`` when the list
    cannot be read or the ticker is not a plausible TIDM: no verdict either way."""
    if not _SAFE_TICKER_RE.match(ticker or ""):
        return None, None
    return await find_listing(name=name, ticker=ticker, venue="LSE", cfg=cfg,
                              fetcher=fetcher)


#: Words a full legal name may END with after its acronym ("TSMC" for "Taiwan
#: Semiconductor Manufacturing Company Limited").
_ACRONYM_TAIL = frozenset({"limited", "ltd", "inc", "corp", "corporation", "co", "plc",
                           "company", "holdings", "holding", "group", "sa", "ag", "nv"})


def _acronym_agrees(short: str | None, full: str | None) -> bool:
    """An exchange's acronym name for a company whose TICKER already matched: "TSMC" is
    "Taiwan Semiconductor Manufacturing Company Limited", "TCC" is "Taiwan Cement
    Corp.". The acronym must spell the initials of the full name's words in order,
    every word used, except legal words at the end. Three to six letters only."""
    acronym = re.sub(r"[^a-z]", "", (short or "").lower())
    if not 3 <= len(acronym) <= 6 or " " in (short or "").strip():
        return False
    words = re.findall(r"[a-z0-9]+", (full or "").lower())
    for end in range(len(words), 0, -1):
        head, tail = words[:end], words[end:]
        if all(w in _ACRONYM_TAIL for w in tail) and "".join(w[0] for w in head) == acronym:
            return True
    return False


def _names_agree(a: str, b: str, *, ticker_matched: bool = False) -> bool:
    """Equal after normalisation, or one is the other plus generic trailing words
    ("kering" vs "kering sa"; "salvatore ferragamo" vs "salvatore ferragamo spa");
    exchange abbreviations are expanded first ("hermes intl" = "hermes international").

    When the TICKER on the venue already matched, an exchange's shortened name also
    agrees if its words appear, in order, within the legal name — at least three words of
    three or more letters ("bains mer monaco" within "societe anonyme des bains de mer
    et du cercle des etrangers a monaco"). A name-only search never uses this rule.
    """
    if not a or not b:
        return False
    a, b = _expand(a), _expand(b)
    if a == b:
        return True
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 1 and long_[: len(short)] == short and len(" ".join(short)) >= 4:
        return True
    if not ticker_matched:
        return False
    # Exchanges drop LEADING words too ("AIR LIQUIDE" for L'Air Liquide, "SAINT GOBAIN"
    # for Compagnie de Saint-Gobain): once the ticker matched, one shared DISTINCTIVE
    # word (≥4 letters, not generic) agrees. "Gold Mining" vs "Gold Rock Mining" does not.
    from app.services.discovery.identity import _GENERIC_TOKENS

    distinctive = {t for t in short if len(t) >= 4 and t not in _GENERIC_TOKENS}
    if distinctive & set(long_):
        return True
    if len(short) < 3 or any(len(t) < 3 for t in short):
        return False
    remaining = iter(long_)
    return all(token in remaining for token in short)


async def official_market_cap(
    listing: DirectoryListing, *, cfg: Any = None, fetcher: Any = None
) -> tuple[float | None, str | None, str | None, str]:
    """(amount, currency, source_url, basis) from the exchange itself, or Nones.

    ASX publishes market cap; Euronext publishes the closing price in its list and the
    ADMITTED SHARES on each instrument's factsheet — their product is the exchange's
    own market capitalisation.
    """
    if listing.market_cap:
        return listing.market_cap, listing.currency, listing.source_url, (
            f"market capitalisation published in the {listing.directory.upper()} directory"
        )
    if listing.directory == "twse":
        return await _twse_market_cap(listing, cfg=cfg, fetcher=fetcher)
    if listing.directory == "euronext" and listing.isin and listing.mic and listing.price:
        shares, url = await euronext_admitted_shares(listing.isin, listing.mic, cfg=cfg,
                                                     fetcher=fetcher)
        if shares:
            return shares * listing.price, listing.currency, url, (
                f"admitted shares {shares:,.0f} × official closing price "
                f"{listing.price:g} {listing.currency} (Euronext)"
            )
    return None, None, None, "the exchange does not publish a market capitalisation here"


async def _twse_market_cap(
    listing: DirectoryListing, *, cfg: Any = None, fetcher: Any = None
) -> tuple[float | None, str | None, str | None, str]:
    """Issued common shares (company file) × the official closing price (daily file).

    A TDR (code 91xx) is left without one: its file states the UNDERLYING shares, not
    the depositary receipts the price is quoted for."""
    none = (None, None, None, "the exchange does not publish a market capitalisation here")
    if not listing.shares or listing.ticker.startswith("91"):
        return none
    prices = await load(TWSE_PRICES, cfg=cfg, fetcher=fetcher)
    quote = next((p for p in prices if p.ticker == listing.ticker), None)
    if quote is None or not quote.price:
        return none
    return listing.shares * quote.price, "TWD", TWSE_PRICES.url, (
        f"issued common shares {listing.shares:,.0f} × official closing price "
        f"{quote.price:g} TWD on {quote.as_of} (TWSE). {TWSE_ATTRIBUTION}"
    )


SEC_DATA_HOST = "data.sec.gov"
#: The 10-K float is as of the last business day of the issuer's second fiscal quarter.
#: The next 10-K, which replaces it, is due up to 90 days after the NEXT fiscal year-end
#: (plus a 15-day NT 10-K extension): 6 + 12 months + 105 days ≈ 652 days. A current
#: filer's float is therefore up to ~21.5 months old just before its next 10-K; anything
#: older than this belongs to a lapsed filer.
#:
#: QA campaign 2026-11: this was 548 days (18 months). Rockwell Automation (September
#: year-end, float dated 2025-03-31, 10-K filed 2025-11-12) and Symbotic fell outside it
#: in November 2026, so a USD 29bn company passed a "small and mid-cap" thesis as "size:
#: not verified". Every December year-end filer would fall outside it each January until
#: its February 10-K.
MAX_FLOAT_AGE_DAYS = 670


async def sec_public_float(
    cik: str | None, *, cfg: Any = None, fetcher: Any = None
) -> tuple[float, str, str] | None:
    """(public float USD, its as-of date, source URL) from the latest 10-K cover, or None.

    ``dei:EntityPublicFloat`` is the issuer's own statement to its regulator. It is a
    LOWER BOUND on market capitalisation (affiliates' shares are excluded) — see
    ``constraints.FLOAT_FLOOR_METHOD`` for what it may and may not prove.
    """
    from app.services.sources.document_fetcher import safe_fetch_document

    if not cik or not str(cik).isdigit():
        return None
    url = (f"https://{SEC_DATA_HOST}/api/xbrl/companyconcept/CIK{int(cik):010d}"
           "/dei/EntityPublicFloat.json")
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(SEC_DATA_HOST,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("application/json",),
        )
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        facts = json.loads(content or b"{}").get("units", {}).get("USD") or []
        annual = [f for f in facts if isinstance(f, dict)
                  and str(f.get("form", "")).startswith("10-K")
                  and isinstance(f.get("val"), (int, float)) and f["val"] > 0
                  and isinstance(f.get("end"), str)]
        if not annual:
            return None
        latest = max(annual, key=lambda f: (f["end"], str(f.get("filed") or "")))
        as_of = date.fromisoformat(latest["end"][:10])
    except Exception:  # noqa: BLE001 - an unreadable filing is an absent figure
        return None
    # A float older than this describes another company-year (a lapsed 10-K filer, a
    # switch to 20-F): it proves nothing about today's size.
    if (datetime.now(timezone.utc).date() - as_of).days > MAX_FLOAT_AGE_DAYS:
        return None
    return float(latest["val"]), as_of.isoformat(), url


#: V3.21 acceptance (run 09567859): a thesis that says "growing" makes growth a hard
#: check, and a US small cap with no held filings had no revenue pair to verify it.
#: The issuer's own annual XBRL revenue is on data.sec.gov. Concepts in order of use.
_REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
#: An annual figure covers 350–380 days; anything else is a quarter or a stub period.
_ANNUAL_DAYS = (350, 380)


def annual_usd_facts_by_end(
    payload: dict[str, Any], *, positive_only: bool = True
) -> dict[str, dict]:
    """Every fiscal-year USD fact of one companyconcept payload, keyed by period end (ISO).

    Only 10-K (or 10-K/A) facts with a one-year duration count. For a period filed more
    than once, the latest filing wins. Each value carries ``_end`` (date) and ``_val``
    (float). ``positive_only=False`` keeps zero and negative values (a net loss).
    """
    facts = (payload.get("units") or {}).get("USD") or []
    by_end: dict[str, dict] = {}
    for fact in facts:
        if not isinstance(fact, dict) or not str(fact.get("form", "")).startswith("10-K"):
            continue
        try:
            start = date.fromisoformat(str(fact["start"])[:10])
            end = date.fromisoformat(str(fact["end"])[:10])
            value = float(fact["val"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (_ANNUAL_DAYS[0] <= (end - start).days <= _ANNUAL_DAYS[1]):
            continue
        if positive_only and value <= 0:
            continue
        key = end.isoformat()
        if key not in by_end or str(fact.get("filed") or "") > str(by_end[key].get("filed") or ""):
            by_end[key] = {**fact, "_end": end, "_val": value}
    return by_end


def annual_revenue_pair(payload: dict[str, Any]) -> tuple[dict, dict] | None:
    """The newest two consecutive fiscal-year USD revenues from one companyconcept payload.

    Only 10-K (or 10-K/A) facts with a one-year duration count. For a period filed more
    than once, the latest filing wins. The pair must be consecutive: the prior year ends
    within 380 days before the newer one.
    """
    by_end = annual_usd_facts_by_end(payload)
    ends = sorted(by_end, reverse=True)
    if len(ends) < 2:
        return None
    newer, older = by_end[ends[0]], by_end[ends[1]]
    if not (300 <= (newer["_end"] - older["_end"]).days <= 380):
        return None
    return newer, older


#: Year-to-date interim durations a 10-Q reports (one quarter up to nine months).
_INTERIM_DAYS = (80, 285)
#: A comparative period ends one year earlier, give or take a 52/53-week calendar.
_YEAR_APART_DAYS = (355, 375)
#: Two interim durations this close are the same length of period.
_SAME_LENGTH_DAYS = 10


def interim_revenue_pair(payload: dict[str, Any]) -> tuple[dict, dict] | None:
    """The newest year-to-date 10-Q revenue and the same period one year earlier.

    ADR-118 (validation 2026-11-24): the annual pair said Rapid7 grew +1.9% (FY2025 vs
    FY2024) while its 2026 10-Qs showed nine-month revenue down 1.5% — and discovery
    passed it as "growing". The newest 10-Q states its own comparative period; the
    longest year-to-date duration at the newest end is used (nine months, not the
    quarter), and its comparative must have the same length and end a year earlier.
    """
    facts = (payload.get("units") or {}).get("USD") or []
    rows: list[dict] = []
    for fact in facts:
        if not isinstance(fact, dict) or not str(fact.get("form", "")).startswith("10-Q"):
            continue
        try:
            start = date.fromisoformat(str(fact["start"])[:10])
            end = date.fromisoformat(str(fact["end"])[:10])
            value = float(fact["val"])
        except (KeyError, TypeError, ValueError):
            continue
        days = (end - start).days
        if not (_INTERIM_DAYS[0] <= days <= _INTERIM_DAYS[1]) or value <= 0:
            continue
        rows.append({**fact, "_start": start, "_end": end, "_val": value, "_days": days})
    if not rows:
        return None
    newest_end = max(r["_end"] for r in rows)
    at_newest = [r for r in rows if r["_end"] == newest_end]
    longest = max(r["_days"] for r in at_newest)
    newer = max((r for r in at_newest if r["_days"] == longest),
                key=lambda r: str(r.get("filed") or ""))
    comparatives = [
        r for r in rows
        if _YEAR_APART_DAYS[0] <= (newest_end - r["_end"]).days <= _YEAR_APART_DAYS[1]
        and abs(r["_days"] - newer["_days"]) <= _SAME_LENGTH_DAYS
    ]
    if not comparatives:
        return None
    older = max(comparatives, key=lambda r: str(r.get("filed") or ""))
    return newer, older


def latest_revenue_pair(payload: dict[str, Any]) -> tuple[dict, dict, str] | None:
    """``(newer, older, kind)``: the interim pair when it is newer than the annual one."""
    annual = annual_revenue_pair(payload)
    interim = interim_revenue_pair(payload)
    if interim is not None and (annual is None or interim[0]["_end"] > annual[0]["_end"]):
        return interim[0], interim[1], "interim_ytd"
    if annual is not None:
        return annual[0], annual[1], "annual"
    return None


def _period_label(fact: dict, kind: str) -> str:
    if kind == "annual":
        return f"FY ending {fact['_end'].isoformat()}"
    months = max(1, round(fact["_days"] / 30.44))
    return f"{months}M ending {fact['_end'].isoformat()}"


async def sec_revenue_growth(
    cik: str | None, *, cfg: Any = None, fetcher: Any = None, today: date | None = None,
) -> Any:
    """A verified ``revenue_pair`` growth observation from the issuer's annual XBRL, or None.

    Never raises. At most one read per concept, stopping at the first concept with a pair.
    A newest fiscal year that ended more than 550 days ago is a lapsed filer: no figure.
    """
    from app.services.discovery import constraints as cons
    from app.services.sources.document_fetcher import safe_fetch_document

    if not cik or not str(cik).isdigit():
        return None
    now = today or datetime.now(timezone.utc).date()
    for concept in _REVENUE_CONCEPTS:
        url = (f"https://{SEC_DATA_HOST}/api/xbrl/companyconcept/CIK{int(cik):010d}"
               f"/us-gaap/{concept}.json")
        try:
            result = await (fetcher or safe_fetch_document)(
                url, allowed_domains=(SEC_DATA_HOST,), cfg=cfg, resolve_ip=True,
                extra_text_content_types=("application/json",),
            )
            content = getattr(result, "content", None) if getattr(result, "ok", False) else None
            pair = latest_revenue_pair(json.loads(content)) if content else None
        except Exception:  # noqa: BLE001 - an unreadable concept is an absent figure
            pair = None
        if pair is None:
            continue
        newer, older, kind = pair
        if (now - newer["_end"]).days > 550:
            return None
        return cons.growth_from_revenue_pair(
            newer["_val"], older["_val"], period=_period_label(newer, kind),
            base_period=_period_label(older, kind), source_url=url,
            source_tier="T1_primary_filing", verified=True,
        )
    return None


#: V3.20 slice 1.5 (live finding 2026-11-22). A current filer restates its cover-page
#: share count on every 10-Q and 10-K, at most ~135 days apart; older than this belongs
#: to a lapsed or late filer and is not today's share count.
MAX_COVER_SHARES_AGE_DAYS = 200
#: The close multiplied must be recent: a week covers weekends and market holidays.
MAX_PRICE_AGE_DAYS = 7
#: Domestic filers' periodic reports. A 20-F / 40-F filer's cover counts ORDINARY shares
#: while its US line may be an ADS at a ratio, so their product is not a market cap.
_DOMESTIC_COVER_FORMS = ("10-K", "10-Q", "10-KT", "10-QT")


@dataclass(frozen=True)
class SharesTimesPrice:
    """A US domestic filer's market cap: its own cover-page share count × a dated close."""

    amount: float
    shares: float
    shares_as_of: str
    shares_form: str
    shares_url: str
    price: float
    price_date: str
    price_url: str
    price_tier: str

    def components(self) -> dict[str, Any]:
        return {
            "shares": self.shares, "shares_as_of": self.shares_as_of,
            "shares_form": self.shares_form, "shares_source_url": self.shares_url,
            "shares_concept": "dei:EntityCommonStockSharesOutstanding",
            "price": self.price, "price_currency": "USD", "price_date": self.price_date,
            "price_source_url": self.price_url, "price_source_tier": self.price_tier,
        }


async def eodhd_last_close(
    ticker: str, *, today: date | None = None
) -> tuple[float, str, str, str] | None:
    """(close USD, its date, source URL, tier) of a US listing's latest close, or None.

    The key stays inside the provider (a query parameter it adds itself), so no URL here
    or in an error carries it. No key, an unknown symbol or any failure is None.
    """
    from datetime import timedelta

    from app.integrations.financial_data_provider import ProviderStatus
    from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider

    provider = EodhdPriceOnlyProvider()
    if provider.get_provider_status() != ProviderStatus.ok:
        return None
    end = today or datetime.now(timezone.utc).date()
    try:
        history = await provider.get_price_history(
            ticker, "US", start_date=(end - timedelta(days=MAX_PRICE_AGE_DAYS + 3)).isoformat(),
            end_date=end.isoformat(),
        )
    except Exception:  # noqa: BLE001 - an unreadable price is an absent figure
        return None
    points = [p for p in history.price_points if p.close and p.close > 0]
    if not points:
        return None
    last = max(points, key=lambda p: str(p.date))
    return (float(last.close), str(last.date)[:10], str(history.source_url),
            provider.source_tier.value)


async def sec_shares_x_price(
    cik: str | None,
    ticker: str | None,
    *,
    cfg: Any = None,
    fetcher: Any = None,
    price_loader: Any = None,
    today: date | None = None,
) -> tuple[SharesTimesPrice | None, str]:
    """``(figure, basis)`` or ``(None, reason)``. Never raises.

    The issuer's own cover-page ``dei:EntityCommonStockSharesOutstanding`` (latest 10-Q
    or 10-K) × the latest close of its US line. Refused for a foreign private issuer's
    cover (ADS ratio), for more than one share class (one line's price does not value the
    others), and for a share count or a close older than the limits above.
    """
    from app.services.passive_ownership.view import cover_shares_url, parse_cover_shares
    from app.services.sources.document_fetcher import safe_fetch_document

    if not cik or not str(cik).isdigit() or not ticker:
        return None, "no CIK and ticker to read the share count and price by"
    url = cover_shares_url(str(cik))
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(SEC_DATA_HOST,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("application/json",),
        )
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        cover = parse_cover_shares(json.loads(content or b"{}"), source_url=url)
    except Exception:  # noqa: BLE001 - an unreadable filing is an absent figure
        return None, "the cover-page share count could not be read"
    if cover is None:
        return None, "no cover-page share count is filed"
    form = str(cover.form or "")
    if form.split("/")[0] not in _DOMESTIC_COVER_FORMS:
        return None, f"the share count is from a {form or 'non-periodic'} cover, not a 10-Q/10-K"
    if cover.classes != 1:
        return None, f"{cover.classes} share classes: one line's price does not value them all"
    now = today or datetime.now(timezone.utc).date()
    if (now - cover.as_of).days > MAX_COVER_SHARES_AGE_DAYS:
        return None, f"the newest cover-page share count is dated {cover.as_of.isoformat()}"
    try:
        price = await (price_loader or eodhd_last_close)(str(ticker), today=now)
    except Exception:  # noqa: BLE001
        price = None
    if not price:
        return None, "no recent close price for the US listing"
    close, price_date, price_url, price_tier = price
    try:
        age = (now - date.fromisoformat(price_date[:10])).days
    except ValueError:
        return None, "the close price carries no date"
    if age > MAX_PRICE_AGE_DAYS or age < 0:
        return None, f"the newest close price is dated {price_date}"
    shares = float(cover.shares)
    figure = SharesTimesPrice(
        amount=shares * close, shares=shares, shares_as_of=cover.as_of.isoformat(),
        shares_form=form, shares_url=url, price=close, price_date=price_date[:10],
        price_url=price_url, price_tier=price_tier,
    )
    return figure, (f"cover-page shares {shares:,.0f} ({form}, {figure.shares_as_of}) × "
                    f"close {close:g} USD on {figure.price_date}")


#: CIK → (country, url) or None, for today: one submissions read per registrant per day.
@dataclass(frozen=True)
class RegistrantDomicile:
    """What an SEC registrant's own record says about WHERE the issuer is.

    ``incorporation``: the country of incorporation (its legal domicile). ``business``: the
    country of its business address (where it is run). Either may be None.
    """

    incorporation: str | None
    business: str | None
    url: str


_DOMICILE_CACHE: dict[tuple[str, str], RegistrantDomicile | None] = {}
#: EDGAR writes a US state as two letters; every foreign code carries a digit (I0, X0, 1T).
_US_STATE_CODE = re.compile(r"^[A-Z]{2}$")
#: EDGAR's own country texts that are not the registry's names.
_EDGAR_COUNTRY_TEXT = {
    "korea, republic of": "South Korea",
    "virgin islands, british": "British Virgin Islands",
    "taiwan, province of china": "Taiwan",
    "russian federation": "Russia",
}


def edgar_country(text: Any) -> str | None:
    """EDGAR's country text as the registry's country name: "Canada (Federal Level)" and
    "Ontario, Canada" are Canada; "Korea, Republic of" is South Korea."""
    from app.services.discovery.identity import ISO2_COUNTRY
    from app.services.exchange_registry import COUNTRY_TO_REGION

    if not isinstance(text, str):
        return None
    raw = re.sub(r"\s*\(.*?\)\s*", " ", text).strip()
    if not raw:
        return None
    known = {c.lower(): c for c in (*COUNTRY_TO_REGION, *ISO2_COUNTRY.values())}
    low = raw.lower()
    if low in _EDGAR_COUNTRY_TEXT:
        return _EDGAR_COUNTRY_TEXT[low]
    if low in known:
        return known[low]
    tail = low.rsplit(",", 1)[-1].strip()
    if tail in known:
        return known[tail]
    return raw.title()[:60]


async def sec_registrant_domicile(
    cik: str | None, *, cfg: Any = None, fetcher: Any = None
) -> RegistrantDomicile | None:
    """Where an SEC registrant's own record places the issuer, or None. Never raises.

    ``stateOfIncorporation`` is a US state's two-letter code or an EDGAR foreign code (a
    letter and a digit: ``I0`` France) described by ``stateOfIncorporationDescription``. The
    field is not always right — Shell plc is recorded as ``DC`` — so a US state code counts
    only when the business address is not a foreign location. An empty incorporation (Cameco)
    leaves the business address as the only statement.
    """
    from app.services.sources.document_fetcher import safe_fetch_document

    if not cik or not re.fullmatch(r"[0-9]{1,10}", str(cik)):
        return None
    key = (str(int(cik)), _today())
    if key in _DOMICILE_CACHE:
        return _DOMICILE_CACHE[key]
    url = f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(SEC_DATA_HOST,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("application/json",),
        )
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        data = json.loads(content) if content else None
    except Exception:  # noqa: BLE001 - an unreadable record is no verdict (not cached)
        return None
    if not isinstance(data, dict):
        return None
    addresses = data.get("addresses")
    business_address = addresses.get("business") if isinstance(addresses, dict) else None
    if not isinstance(business_address, dict):
        business_address = {}
    business: str | None = None
    if business_address.get("isForeignLocation") == 1:
        business = edgar_country(business_address.get("country"))
    else:
        state = str(business_address.get("stateOrCountry") or "").strip().upper()
        if _US_STATE_CODE.match(state):
            business = "United States"
        elif state:
            business = edgar_country(business_address.get("stateOrCountryDescription"))
    code = str(data.get("stateOfIncorporation") or "").strip().upper()
    incorporation: str | None = None
    if _US_STATE_CODE.match(code):
        if business in (None, "United States"):
            incorporation = "United States"
    elif code:
        incorporation = edgar_country(data.get("stateOfIncorporationDescription"))
    found = (RegistrantDomicile(incorporation, business, url)
             if incorporation or business else None)
    _DOMICILE_CACHE[key] = found
    return found


_ANNUAL_ACCESSIONS_CACHE: dict[tuple[str, str], frozenset[str] | None] = {}
#: monotonic time of the last failed submissions read; no retry before FAILURE_RETRY_SECONDS,
#: so a slow or refusing data.sec.gov costs one timeout, not one per statement.
_ACCESSIONS_FAILED_AT: dict[str, float] = {}
_ANNUAL_FORMS = frozenset({
    "10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A",
})


async def sec_annual_accessions(
    cik: str | None, *, cfg: Any = None, fetcher: Any = None
) -> frozenset[str] | None:
    """Accession numbers of the annual reports in an SEC registrant's recent filings, or
    None when the record cannot be read. Never raises.

    A consolidated subsidiary that registers its own debt or preferred stock usually files
    a COMBINED annual report with its parent (Entergy Louisiana inside Entergy's 10-K),
    under the parent's accession number. Two registrants sharing one is one reporting group.
    """
    from app.services.sources.document_fetcher import safe_fetch_document

    if not cik or not re.fullmatch(r"[0-9]{1,10}", str(cik)):
        return None
    key = (str(int(cik)), _today())
    if key in _ANNUAL_ACCESSIONS_CACHE:
        return _ANNUAL_ACCESSIONS_CACHE[key]
    failed = _ACCESSIONS_FAILED_AT.get(SEC_DATA_HOST)
    if failed is not None and time.monotonic() - failed < FAILURE_RETRY_SECONDS:
        return None
    url = f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(SEC_DATA_HOST,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("application/json",),
        )
    except Exception:  # noqa: BLE001 - the fetcher is documented never to raise; belt and braces
        result = None
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        # The HOST did not serve the record (timeout, 403/429, 5xx — returned as ok=False,
        # never raised): back off, so a slow data.sec.gov costs one deadline per window.
        _ACCESSIONS_FAILED_AT[SEC_DATA_HOST] = time.monotonic()
        return None
    try:
        data = json.loads(content)
        recent = data["filings"]["recent"]
        forms, accessions = recent["form"], recent["accessionNumber"]
    except Exception:  # noqa: BLE001 - one malformed RECORD is no verdict for it alone (not cached)
        return None
    if not isinstance(forms, list) or not isinstance(accessions, list):
        return None
    found = frozenset(
        str(accn) for form, accn in zip(forms, accessions, strict=False)
        if str(form) in _ANNUAL_FORMS
    )
    _ANNUAL_ACCESSIONS_CACHE[key] = found
    return found


async def euronext_admitted_shares(
    isin: str, mic: str, *, cfg: Any = None, fetcher: Any = None
) -> tuple[float | None, str | None]:
    from app.services.sources.document_fetcher import safe_fetch_document

    url = (f"https://live.euronext.com/en/ajax/getFactsheetInfoBlock/STOCK/"
           f"{isin}-{mic}/fs_tradinginfo_block")
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(EURONEXT.host,), cfg=cfg, resolve_ip=True,
        )
    except Exception:  # noqa: BLE001
        return None, None
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        return None, None
    text = re.sub(r"<[^>]+>", " ", content.decode("utf-8", "replace"))
    # ONE grouped number ("123,420,778" or "123 420 778"); a following figure such as a
    # year must never be glued onto it.
    match = re.search(r"Admitted shares\s+(\d{1,3}(?:[, \u00a0]\d{3})*)(?!\d)", text)
    if not match:
        return None, url
    return _num(re.sub(r"[, \u00a0]", "", match.group(1))), url


__all__ = [
    "DIRECTORIES",
    "DIRECTORY_FOR_VENUE",
    "DirectoryListing",
    "find_listing",
    "load",
    "official_market_cap",
    "parse_asx",
    "parse_euronext",
    "parse_sec",
    "parse_six",
    "parse_tmx",
]
