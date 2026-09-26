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

Directories covered: Euronext (Paris, Milan, Amsterdam, Brussels, Lisbon, Dublin, Oslo
and their growth markets), SIX Swiss Exchange, ASX, TSX, TSX Venture, and the US
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Directory:
    key: str
    host: str
    url: str
    tier: str
    label: str


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
DIRECTORIES: tuple[_Directory, ...] = (EURONEXT, ASX, TSX, TSXV, SIX, SEC)

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
}

_CACHE: dict[tuple[str, str], list[DirectoryListing]] = {}
#: Per-key locks so concurrent leads download a directory once, not once each.
_LOCKS: dict[str, asyncio.Lock] = {}
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


_PARSERS = {
    "euronext": parse_euronext, "asx": parse_asx, "six": parse_six, "sec": parse_sec,
    "tsx": lambda text, source: parse_tmx(text, source, "TO"),
    "tsxv": lambda text, source: parse_tmx(text, source, "V"),
}


async def load(
    directory: _Directory, *, cfg: Any = None, fetcher: Any = None
) -> list[DirectoryListing]:
    """The directory's listings, fetched once per process per day. [] when unreadable."""
    key = (directory.key, _today())
    if key in _CACHE:
        return _CACHE[key]
    async with _LOCKS.setdefault(directory.key, asyncio.Lock()):
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

    try:
        result = await (fetcher or safe_fetch_document)(
            directory.url, allowed_domains=(directory.host,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("text/csv", "application/csv", "application/json",
                                      "text/plain", "application/octet-stream"),
        )
    except Exception:  # noqa: BLE001 - an unreadable directory is a missing source
        return []
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
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


#: Venue suffixes a quoted ticker may carry ("KER.PA"). Anything else is PART of the
#: ticker — "BT.A" is BT Group's TIDM, not BT on venue "A".
_VENUE_SUFFIXES = frozenset({
    "PA", "MI", "AS", "BR", "LS", "IR", "OL", "AX", "AU", "TO", "V", "SW", "US", "L", "LN",
    "CO", "DE", "F", "HE", "ST", "MC", "VX", "CN", "NE", "TSX", "LSE", "ASX",
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
    if venue == "LSE" and ticker:
        return await _lse_listing(name=name, ticker=_strip_suffix(ticker), cfg=cfg,
                                  fetcher=fetcher)
    directory = DIRECTORY_FOR_VENUE.get(venue or "")
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
        by_symbol = [r for r in in_venue if r.ticker == symbol]
        # The symbol is authoritative only when the NAME agrees: a provider can pair a
        # real ticker with the wrong company.
        for row in by_symbol:
            if not wanted or _names_agree(wanted, normalised_name(row.name),
                                          ticker_matched=True):
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


LSE_HOST = "api.londonstockexchange.com"


async def _lse_listing(
    *, name: str | None, ticker: str, cfg: Any = None, fetcher: Any = None
) -> tuple[DirectoryListing | None, str | None]:
    """The London Stock Exchange's own instrument record for a TIDM, with its published
    market capitalisation (GBP). One request per ticker, cached per day."""
    from app.services.sources.document_fetcher import safe_fetch_document

    if not _SAFE_TICKER_RE.match(ticker or ""):
        return None, None  # never interpolate an unexpected string into a request path
    url = f"https://{LSE_HOST}/api/gw/lse/instruments/alldata/{ticker}"
    key = ("lse:" + ticker, _today())
    if key in _CACHE:
        rows = _CACHE[key]
    else:
        try:
            result = await (fetcher or safe_fetch_document)(
                url, allowed_domains=(LSE_HOST,), cfg=cfg, resolve_ip=True,
                extra_text_content_types=("application/json",),
            )
            content = getattr(result, "content", None) if getattr(result, "ok", False) else None
            data = json.loads(content.decode("utf-8", "replace")) if content else None
        except Exception:  # noqa: BLE001 - an unreadable record is no verdict
            return None, None
        if not isinstance(data, dict):
            return None, None
        if not data.get("tidm"):
            # An error or rate-limit body is not "not listed": no verdict, not cached.
            return None, None
        rows = [DirectoryListing(
            name=str(data.get("issuername") or data.get("description") or ""),
            ticker=str(data["tidm"]).upper(), exchange="LSE", isin=data.get("isin"),
            country=data.get("country"), market_cap=_num(data.get("marketcapitalization")),
            currency="GBP", price=_num(data.get("lastclose") or data.get("lastprice")),
            industry=None, mic="XLON", directory="lse", source_url=url, tier=TIER_EXCHANGE,
            as_of=_today(),
        )]
        _store(key, rows)
    if not rows:
        return None, f"the London Stock Exchange lists no instrument {ticker}"
    row = rows[0]
    if name and not _names_agree(normalised_name(name), normalised_name(row.name),
                                 ticker_matched=True):
        return None, f"the London Stock Exchange lists {ticker} as {row.name!r}, not {name!r}"
    return row, None


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
    if listing.directory == "euronext" and listing.isin and listing.mic and listing.price:
        shares, url = await euronext_admitted_shares(listing.isin, listing.mic, cfg=cfg,
                                                     fetcher=fetcher)
        if shares:
            return shares * listing.price, listing.currency, url, (
                f"admitted shares {shares:,.0f} × official closing price "
                f"{listing.price:g} {listing.currency} (Euronext)"
            )
    return None, None, None, "the exchange does not publish a market capitalisation here"


SEC_DATA_HOST = "data.sec.gov"
#: The 10-K float is as of the last Q2 end, so a current filer's is at most ~15 months
#: old; anything older than this is stale.
MAX_FLOAT_AGE_DAYS = 548


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
