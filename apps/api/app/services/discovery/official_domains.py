"""Official issuer domains — the independent proof that a website IS an issuer's.

WHY (owner decision, 2026-10-07)
================================
Discovery rule A3 asks for a fetched passage that ties a verified company to the thesis. A
verified OFFICIAL ISSUER SOURCE may be that passage — but only if the platform has
independently established that the domain is that issuer's. A domain is NEVER trusted
because:

* its hostname resembles the company's name (anyone can register ``lynas-rare-earths.net``);
* the search provider labelled it official;
* a model said so;
* the page itself claims to be the official site.

(``identity.issuer_domain_matches`` is a name-equality heuristic used for LISTING pages; it
is deliberately NOT used here.)

WHAT ESTABLISHES A DOMAIN
=========================
Each source is independent of the page being judged, and each is recorded with its basis:

``verified_issuer_registry``
    the platform's own code-defined registry (``verified_issuer_sources``).
``exchange_profile`` / ``regulator_profile``
    a website the exchange or regulator itself publishes for the listing (ASX company
    profile, SEC submissions ``website`` / ``investorWebsite``) — when it is populated.
``exchange_announcement``
    a document the EXCHANGE published under the issuer's ticker (ASX announcements) that
    prints the issuer's website in its letterhead (``w LynasRareEarths.com``) or under a
    ``Website`` label. The exchange attributes the document to the ticker; the issuer
    states its domain in it. A domain mentioned only in the BODY of a document ("see
    www.example.com") is never taken: only the letterhead / labelled website counts.

Nothing else. A venue with none of these (today: LSE/AIM, Euronext, SIX, TSX) yields no
official domain, so an issuer-only passage cannot carry A3 there — the candidate stays
"also surfaced", never wrongly admitted.

Pure helpers (``domains_in_letterhead``) are separate from the network lookups so the rules
can be tested without a network. Every lookup is bounded, never raises, and is cached per
process per day.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from app.services.discovery.identity import EXCHANGE_HOSTS

BASIS_REGISTRY = "verified_issuer_registry"
BASIS_EXCHANGE_PROFILE = "exchange_profile"
BASIS_REGULATOR_PROFILE = "regulator_profile"
BASIS_EXCHANGE_ANNOUNCEMENT = "exchange_announcement"

ASX_API_HOST = "asx.api.markitdigital.com"
ASX_FILE_HOST = "cdn-api.markitdigital.com"
SEC_DATA_HOST = "data.sec.gov"

#: Announcements sampled per issuer, and the largest file read (a presentation deck is
#: large and its letterhead adds nothing a cover letter does not).
MAX_ANNOUNCEMENTS = 5
MAX_DOCUMENT_BYTES = 2_500_000
#: The letterhead is the top of page one.
LETTERHEAD_CHARS = 700
#: A letterhead printing more distinct domains than this is ambiguous, so it names none.
MAX_LETTERHEAD_DOMAINS = 2

#: Domains that are never an issuer's own: exchanges, regulators, wires, social platforms,
#: aggregators, document and storage hosts. A letterhead that prints one of these is quoting
#: somebody, not naming itself.
_NEVER_AN_ISSUER: tuple[str, ...] = (
    *EXCHANGE_HOSTS,
    "sec.gov", "asic.gov.au", "fca.org.uk", "esma.europa.eu", "europa.eu", "gov.uk", "gov.au",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "google.com", "microsoft.com", "adobe.com", "zoom.us", "teams.microsoft.com", "bit.ly",
    "globenewswire.com", "businesswire.com", "prnewswire.com", "accesswire.com",
    "newsfilecorp.com", "cision.com", "marketscreener.com", "yahoo.com", "reuters.com",
    "bloomberg.com", "wikipedia.org", "computershare.com", "linkmarketservices.com.au",
    "automicgroup.com.au", "automic.com.au", "boardroomlimited.com.au", "advancedshare.com.au",
    "computershare.com.au", "computershare.co.uk", "linkgroup.com", "linkassetservices.com",
    "equiniti.com", "neonsq.com", "securitytransfer.com.au", "xcend.com.au",
)

#: ``w LynasRareEarths.com`` · ``Website: www.example.com.au`` · ``web  example.com``
_LABELLED_DOMAIN = re.compile(
    r"(?:^|[\s|])(?:w|web|www|website|internet)\s*[:.]?\s+"
    r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:[a-z]{2,}))\b",
    re.IGNORECASE,
)
#: a bare ``www.example.com`` token in the letterhead.
_WWW_DOMAIN = re.compile(
    r"\bwww\.([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:[a-z]{2,}))\b", re.IGNORECASE
)


#: a bare domain token in the letterhead ("T. +61 8 9238 8300  igo.com.au"). Only common
#: corporate suffixes, so a file name ("report.pdf") or a sentence end is never read as one.
#: LOWER-CASE only and a label of at least three characters: "Ref.No", "Sydney.Au" and
#: "x=1.com" are not websites.
_BARE_DOMAIN = re.compile(
    r"(?<![@\w./=?&-])([a-z0-9][a-z0-9-]{2,}(?:\.[a-z0-9-]{2,})*\."
    r"(?:com\.au|co\.uk|co\.nz|com|net|org|io|au|uk|ca|eu|de|fr|se|no|dk|fi|nl|ch|it|es))\b"
)


@dataclass(frozen=True)
class OfficialDomain:
    """One independently established official domain of one issuer."""

    domain: str
    basis: str
    source_url: str | None = None
    detail: str | None = None
    as_of: str | None = None
    # Provenance (domain_proofs.DomainProof): inspectable in the admin diagnostics.
    method: str | None = None
    source_class: str | None = None
    document_id: str | None = None
    matched_entity: str | None = None
    matched_identifiers: dict[str, str] | None = None
    confidence: str | None = None
    status: str = "verified_official"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def registrable_domain(host: str | None) -> str | None:
    """The registrable domain by the PUBLIC SUFFIX LIST, or None.

    A tenant of a shared host (``acme.myshopify.com``, ``x.blogspot.com``) is its OWN
    registrable domain, never the shared host's; a bare public suffix (``com.au``, ``id.au``,
    ``edu.au``) is no domain at all. (``identity.registrable_domain`` knows only a few
    second-level labels and is not used here.)
    """
    from tld import get_tld

    text = (host or "").strip().lower().strip(".")
    if not text or "." not in text:
        return None
    try:
        parsed = get_tld(f"https://{text}", fail_silently=True, as_object=True)
    except Exception:  # noqa: BLE001 - an unparseable host names no domain
        return None
    if parsed is None or not parsed.fld or parsed.fld == parsed.tld:
        return None
    return str(parsed.fld)


def _is_never_an_issuer(domain: str) -> bool:
    from app.services.discovery.admission import a3_host_excluded

    d = domain.lower()
    return a3_host_excluded(d) or any(d == bad or d.endswith("." + bad) for bad in _NEVER_AN_ISSUER)


def domains_in_letterhead(text: str | None) -> list[str]:
    """Registrable domains a document's LETTERHEAD (top of page one) prints as its own.

    Only a labelled website (``w …``, ``Website: …``), a ``www.`` token or a bare domain
    inside the first :data:`LETTERHEAD_CHARS` characters counts (a bare domain there, beside
    the phone and fax lines, included). A domain in the body is a reference to
    somebody (a regulator, a registry, a partner) and is ignored, as is any domain on the
    never-an-issuer list. Order is first appearance; duplicates collapse.
    """
    head = (text or "")[:LETTERHEAD_CHARS]
    found: list[str] = []
    for pattern in (_LABELLED_DOMAIN, _WWW_DOMAIN, _BARE_DOMAIN):
        for match in pattern.finditer(head):
            domain = registrable_domain(match.group(1).lower().rstrip("."))
            if not domain or "." not in domain or _is_never_an_issuer(domain):
                continue
            if domain not in found:
                found.append(domain)
    return found


def domain_covers(official: str, host: str | None) -> bool:
    """``host`` is the official domain or a subdomain of it — never a lookalike."""
    h = (host or "").lower().strip(".").removeprefix("www.")
    d = official.lower().strip(".").removeprefix("www.")
    return bool(h and d) and (h == d or h.endswith("." + d))


#: Hosts of an official domain that speak for the ISSUER: the apex, ``www`` and the investor
#: relations sites. A ``forum.``, ``community.``, ``blog.`` or ``support.`` subdomain can carry
#: other people's words, so it is not the company's voice.
_ISSUER_VOICE_LABELS = ("www", "ir", "investor", "investors", "investorrelations")


def is_issuer_voice(official: str, host: str | None) -> bool:
    """``host`` is the official domain itself, its ``www``, or an investor-relations host."""
    h = (host or "").lower().strip(".")
    d = official.lower().strip(".").removeprefix("www.")
    if not h or not d:
        return False
    return h == d or any(h == f"{label}.{d}" for label in _ISSUER_VOICE_LABELS)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #

_CACHE: dict[tuple[str, str, str, str], list[OfficialDomain]] = {}


def reset_cache() -> None:
    _CACHE.clear()
    _RELATIONS.clear()
    _DOCS.clear()


def _today() -> str:
    return date.today().isoformat()


def _safe_code(ticker: str | None) -> str | None:
    code = (ticker or "").strip().upper()
    return code if re.fullmatch(r"[A-Z0-9]{1,6}", code) else None


async def _fetch(url: str, host: str, *, cfg: Any, fetcher: Any, json_body: bool = False) -> Any:
    from app.services.sources.document_fetcher import safe_fetch_document

    extra = ("application/json", "text/json", "text/plain") if json_body else ()
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(host,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=extra,
        )
    except Exception:  # noqa: BLE001 - an unreadable source is a missing source
        return None
    return result if getattr(result, "ok", False) and getattr(result, "content", None) else None


def _pdf_first_page_text(content: bytes) -> str:
    if len(content) > MAX_DOCUMENT_BYTES or not content.startswith(b"%PDF"):
        return ""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(content))
        return (reader.pages[0].extract_text() or "")[:4000] if reader.pages else ""
    except Exception:  # noqa: BLE001 - an unreadable PDF names no domain
        return ""


def _file_size_kb(label: Any) -> int:
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(KB|MB)?", str(label or ""), re.IGNORECASE)
    if not match:
        return 10**9
    value = float(match.group(1))
    return int(value * (1024 if (match.group(2) or "KB").upper() == "MB" else 1))


#: Documents lodged under a ticker by SOMEBODY ELSE, or forms whose letterhead is not the
#: issuer's own: substantial-holder notices (Forms 603/604/605), takeover and bidder documents,
#: and director's-interest notices.
_THIRD_PARTY_HEADLINE = re.compile(
    r"substantial\s+(?:holder|holding)|becoming\s+a\s+substantial|ceasing\s+to\s+be\s+a\s+"
    r"substantial|\bform\s*60[3-5]\b|\btakeover\b|\bbidder\b|director'?s?['’]?\s*interest|"
    r"\bappendix\s*3[xyz]\b",
    re.IGNORECASE,
)


def _third_party(item: dict[str, Any]) -> bool:
    haystack = f"{item.get('headline') or ''} {item.get('announcementType') or ''}"
    return bool(_THIRD_PARTY_HEADLINE.search(haystack))


#: pypdf on a hostile file is CPU: bounded in concurrency and in time.
_PDF_SLOTS = asyncio.Semaphore(3)
PDF_PARSE_TIMEOUT_SECONDS = 15.0


async def _pdf_text(content: bytes) -> str:
    async with _PDF_SLOTS:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_pdf_first_page_text, content), PDF_PARSE_TIMEOUT_SECONDS
            )
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - an unreadable PDF names no domain
            return ""


async def _asx(code: str, *, cfg: Any, fetcher: Any) -> list[OfficialDomain]:
    out: list[OfficialDomain] = []
    about = f"https://{ASX_API_HOST}/asx-research/1.0/companies/{code}/about"
    result = await _fetch(about, ASX_API_HOST, cfg=cfg, fetcher=fetcher, json_body=True)
    if result is not None:
        try:
            data = json.loads(result.content.decode("utf-8", "replace"))
            site = str(((data.get("data") or data) or {}).get("websiteUrl") or "").strip()
        except (ValueError, AttributeError):
            site = ""
        domain = registrable_domain(re.sub(r"^https?://", "", site).split("/")[0].lower())
        if domain and not _is_never_an_issuer(domain):
            out.append(OfficialDomain(domain, BASIS_EXCHANGE_PROFILE, about,
                                      "the exchange's own company profile", _today()))
    if out:
        return out

    listing = f"https://{ASX_API_HOST}/asx-research/1.0/companies/{code}/announcements"
    result = await _fetch(listing, ASX_API_HOST, cfg=cfg, fetcher=fetcher, json_body=True)
    if result is None:
        return out
    try:
        items = (json.loads(result.content.decode("utf-8", "replace")).get("data") or {}).get(
            "items"
        ) or []
    except (ValueError, AttributeError):
        return out
    # Announcements the ISSUER wrote. A holder's notice, a bidder's document and a director's
    # form are lodged UNDER the target's ticker but carry somebody else's letterhead.
    own = [
        i for i in items
        if isinstance(i, dict) and i.get("documentKey") and not _third_party(i)
    ]
    small = sorted(own, key=lambda i: _file_size_kb(i.get("fileSize")))[:MAX_ANNOUNCEMENTS]
    seen: dict[str, list[str]] = {}
    self_referencing: set[str] = set()
    ticker_ref = re.compile(rf"\bASX\s*(?:code)?\s*[:\-]?\s*{re.escape(code)}\b", re.IGNORECASE)
    for item in small:
        key = str(item["documentKey"])
        if not re.fullmatch(r"[A-Za-z0-9-]{6,60}", key):
            continue
        file_url = f"https://{ASX_FILE_HOST}/apiman-gateway/ASX/asx-research/1.0/file/{key}"
        doc = await _fetch(file_url, ASX_FILE_HOST, cfg=cfg, fetcher=fetcher)
        if doc is None:
            continue
        text = await _pdf_text(doc.content)
        domains = domains_in_letterhead(text)
        if len(domains) > MAX_LETTERHEAD_DOMAINS:
            continue  # a letterhead naming several sites is ambiguous: take none of them
        for domain in domains:
            seen.setdefault(domain, []).append(file_url)
            if ticker_ref.search(text):
                self_referencing.add(domain)
    for domain, urls in seen.items():
        # A single letterhead is enough only when that same document also names the ticker
        # as its own ("ASX: LYC"); otherwise the domain must repeat across announcements.
        if len(set(urls)) < 2 and domain not in self_referencing:
            continue
        out.append(OfficialDomain(
            domain, BASIS_EXCHANGE_ANNOUNCEMENT, urls[0],
            "printed in the letterhead of "
            f"{len(set(urls))} announcement(s) the exchange published under the ticker {code}",
            _today(),
        ))
    return out


async def _sec_submissions(cik: str, *, cfg: Any, fetcher: Any) -> dict[str, Any] | None:
    if not re.fullmatch(r"\d{1,10}", str(cik or "")):
        return None
    url = f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"
    result = await _fetch(url, SEC_DATA_HOST, cfg=cfg, fetcher=fetcher, json_body=True)
    if result is None:
        return None
    try:
        data = json.loads(result.content.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _sec(cik: str, *, cfg: Any, fetcher: Any, data: dict[str, Any] | None = None
               ) -> list[OfficialDomain]:
    if data is None:
        data = await _sec_submissions(cik, cfg=cfg, fetcher=fetcher)
    if data is None:
        return []
    url = f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"
    out: list[OfficialDomain] = []
    for field_name in ("website", "investorWebsite"):
        site = str(data.get(field_name) or "").strip()
        domain = registrable_domain(re.sub(r"^https?://", "", site).split("/")[0].lower())
        if domain and not _is_never_an_issuer(domain) and not any(
            o.domain == domain for o in out
        ):
            out.append(OfficialDomain(domain, BASIS_REGULATOR_PROFILE, url,
                                      f"the SEC's own submissions record ({field_name})",
                                      _today()))
    return out



# --------------------------------------------------------------------------- #
# Generic resolver: authoritative DOCUMENTS (owner direction 2026-10-26)
# --------------------------------------------------------------------------- #

#: Headlines of ASX documents that carry the issuer's own contact block / website.
_HIGH_VALUE_HEADLINE = re.compile(
    r"annual\s+report|investor\s+presentation|corporate\s+presentation|company\s+presentation"
    r"|quarterly|activities\s+report|project\s+update|company\s+overview|results|half[\s-]year"
    r"|operations?\s+update|investor\s+update",
    re.IGNORECASE,
)
_HEADLINE_PRIORITY = (
    (re.compile(r"annual\s+report", re.IGNORECASE), 0),
    (re.compile(r"investor|corporate|company\s+(?:presentation|overview)", re.IGNORECASE), 1),
    (re.compile(r"quarterly|activities", re.IGNORECASE), 2),
)
#: Bounded: never a crawl of the issuer's announcement history.
MAX_DEEP_DOCS = 4
MAX_SEC_DOCS = 2
MAX_UK_DOCS = 3
MAX_DEEP_PDF_BYTES = 12_000_000
RESOLVER_WALL_SECONDS = 75.0

_ENTITY_CACHE: dict[tuple[str, str, str], Any] = {}
#: (venue, ticker) -> company↔project relationships proven by the issuer's regulated documents.
_RELATIONS: dict[tuple[str, str], list[Any]] = {}
MAX_CACHE_ENTRIES = 400


def _bounded_put(cache: dict[Any, Any], key: Any, value: Any) -> None:
    """Insert with oldest-first eviction, so a long-lived process cannot grow without bound."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > MAX_CACHE_ENTRIES:
        cache.pop(next(iter(cache)))


def relations_for(ticker: str | None, venue: str | None) -> list[Any]:
    """Company↔project relationships an authoritative source proved (this process, today)."""
    return list(_RELATIONS.get(((venue or "").upper(), (ticker or "").upper()), []))


def _headline_rank(headline: str) -> int:
    for pattern, rank in _HEADLINE_PRIORITY:
        if pattern.search(headline or ""):
            return rank
    return 3


MAX_FULL_PAGES = 120
MAX_FULL_TEXT_CHARS = 300_000


def _pdf_bundle(content: bytes) -> tuple[str, str]:
    """``(contact excerpt, body)``: the first and last three pages (cover, corporate
    directory, contact block) and up to ``MAX_FULL_PAGES`` pages for theme passages."""
    if len(content) > MAX_DEEP_PDF_BYTES or not content.startswith(b"%PDF"):
        return "", ""
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(content))
        total = len(reader.pages)
        keep = min(total, MAX_FULL_PAGES)
        head = range(keep - 3 if total > keep else keep)
        idx = sorted(set(head) | set(range(max(0, total - 3), total)))
        pages = {i: (reader.pages[i].extract_text() or "")[:8000] for i in idx}
        picked = list(range(min(3, total))) + [i for i in range(max(3, total - 3), total)]
        excerpt = "\n".join(pages.get(i, "")[:6000] for i in picked)[:40_000]
        return excerpt, "\n\n".join(pages[i] for i in idx)[:MAX_FULL_TEXT_CHARS]
    except Exception:  # noqa: BLE001 - an unreadable PDF names no domain
        return "", ""


def _pdf_front_back_text(content: bytes) -> str:
    return _pdf_bundle(content)[0]


MAX_PDF_BYTES = 12_000_000
MAX_FILING_BYTES = 12_000_000


async def _pdf_deep_bundle(content: bytes) -> tuple[str, str]:
    """Parse off-loop. The slot is held until the THREAD finishes, not until the wait times out:
    a cancelled wait does not stop a running thread, so releasing early would let timeouts pile
    parses up (review M5)."""
    if len(content) > MAX_PDF_BYTES:
        return "", ""
    await _PDF_SLOTS.acquire()
    task = asyncio.ensure_future(asyncio.to_thread(_pdf_bundle, content))
    task.add_done_callback(lambda _t: _PDF_SLOTS.release())
    try:
        return await asyncio.wait_for(asyncio.shield(task), PDF_PARSE_TIMEOUT_SECONDS * 3)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return "", ""


async def _pdf_deep_text(content: bytes) -> str:
    return (await _pdf_deep_bundle(content))[0]


_WEBSITE_HINT = re.compile(r"web\s*site|internet\s+site|www\.", re.IGNORECASE)


def _filing_texts(content: bytes) -> tuple[str, str]:
    """``(website-bearing windows, body)`` of a (possibly 10 MB iXBRL) filing.

    The website statement sits anywhere in the document, so the whole body is scanned, but the
    windows around those mentions are kept for the domain proofs; the stripped body (XBRL
    header, scripts and tags removed) serves theme-passage reading."""
    import html as _html

    raw = content[:MAX_FILING_BYTES].decode("utf-8", "replace")
    raw = re.sub(
        r"(?is)<ix:header>.*?</ix:header>|<(?:script|style)\b.*?</(?:script|style)>", " ", raw
    )
    text = _html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text)
    windows: list[str] = []
    last_end = -1
    for m in _WEBSITE_HINT.finditer(text):
        if m.start() < last_end:
            continue
        lo, hi = max(0, m.start() - 160), min(len(text), m.end() + 200)
        windows.append(text[lo:hi])
        last_end = hi
        if len(windows) >= 60:
            break
    head = text[:3000]  # the cover page: the registrant's name, address, telephone
    return (head + "\n\n" + "\n\n".join(windows))[:120_000], text[:MAX_FULL_TEXT_CHARS]


def _filing_website_text(content: bytes) -> str:
    return _filing_texts(content)[0]


def _html_text(content: bytes) -> str:
    from app.services.sources.disclosures.asx import _text

    return _text(content[:1_500_000].decode("utf-8", "replace"))[:80_000]


def _issuer_named(text: str, name: str, ticker: str | None, *, asx: bool = False) -> bool:
    from app.services.discovery.domain_proofs import EntityIds, _names

    if _names(text, EntityIds(name=name)):
        return True
    return bool(
        asx and ticker and re.search(
            rf"\bASX\s*(?:code)?\s*[:\-]?\s*{re.escape(ticker)}\b", text, re.IGNORECASE
        )
    )


async def _gleif(
    isin: str, *, cfg: Any, fetcher: Any
) -> tuple[str | None, str | None, frozenset[str]]:
    """``(lei, legal name, registration numbers)`` from GLEIF for one ISIN (one record)."""
    from app.services.discovery.domain_proofs import _norm_regno
    from app.services.sources.disclosures.uk_nsm import GLEIF_HOST, GLEIF_ISIN_URL

    result = await _fetch(GLEIF_ISIN_URL.format(isin=isin), GLEIF_HOST, cfg=cfg, fetcher=fetcher,
                          json_body=True)
    if result is None:
        return None, None, frozenset()
    try:
        records = json.loads(result.content).get("data") or []
    except (ValueError, AttributeError):
        return None, None, frozenset()
    if len(records) != 1:
        return None, None, frozenset()
    rec = records[0]
    entity = (rec.get("attributes") or {}).get("entity") or {}
    regno = str(entity.get("registeredAs") or "")
    return (
        str(rec.get("id") or "").upper() or None,
        str((entity.get("legalName") or {}).get("name") or "") or None,
        frozenset({_norm_regno(regno)}) if regno else frozenset(),
    )


async def resolve_entity(
    *, ticker: str | None, venue: str | None, name: str | None, cik: str | None = None,
    cfg: Any = None, fetcher: Any = None,
) -> Any:
    """The verified listed entity with every hard identifier an authoritative source gives it
    (ISIN from the exchange, LEI and company number from GLEIF). **Never raises.**"""
    from app.services.discovery.domain_proofs import EntityIds

    base = EntityIds(name=name or "", ticker=ticker, cik=cik)
    key = ((venue or "").upper(), (ticker or "").upper(), _today())
    if key in _ENTITY_CACHE:
        return _ENTITY_CACHE[key]
    isin: str | None = None
    try:
        v = (venue or "").upper()
        if v in ("AU", "ASX") and _safe_code(ticker):
            result = await _fetch(
                f"https://{ASX_API_HOST}/asx-research/1.0/companies/{_safe_code(ticker)}/key-statistics",
                ASX_API_HOST, cfg=cfg, fetcher=fetcher, json_body=True)
            if result is not None:
                isin = str((json.loads(result.content).get("data") or {}).get("isin") or "") or None
        elif v == "LSE" and ticker:
            from app.services.discovery.directories import _lse_listing

            row, _why = await _lse_listing(name=name, ticker=ticker.upper(), cfg=cfg,
                                           fetcher=fetcher)
            isin = getattr(row, "isin", None)
        lei = legal = None
        regnos: frozenset[str] = frozenset()
        if isin:
            from app.services.discovery.domain_proofs import valid_isin

            if valid_isin(isin):
                lei, legal, regnos = await _gleif(isin, cfg=cfg, fetcher=fetcher)
            else:
                isin = None
        # GLEIF's legal name must agree with the exchange's, or its identifiers are not used.
        if legal and name:
            from app.services.discovery.directories import _names_agree
            from app.services.discovery.identity import normalised_name

            if not _names_agree(normalised_name(legal), normalised_name(name), ticker_matched=True):
                lei, regnos = None, frozenset()
        entity = EntityIds(name=name or "", ticker=ticker, isin=isin, lei=lei,
                           registration_numbers=regnos, cik=cik)
    except Exception:  # noqa: BLE001 - the entity without extra identifiers is still the entity
        entity = base
    if entity.isin or entity.lei or entity.cik or entity.registration_numbers:
        _bounded_put(_ENTITY_CACHE, key, entity)  # a failed resolution is retried, never remembered
    return entity


async def _asx_documents(code: str, entity: Any, *, cfg: Any, fetcher: Any) -> list[Any]:
    """A bounded read of the issuer's high-value ASX documents (annual report, presentations,
    quarterly reports), found on the ASX's own yearly announcement pages — the native
    research feed shows only the latest five items."""
    from app.services.discovery.domain_proofs import DocText
    from app.services.sources.disclosures import asx as asx_mod

    rows: list[dict[str, Any]] = []
    year = date.today().year
    for y in (year, year - 1):
        page = await _fetch(asx_mod.ASX_LISTING_URL.format(code=code, year=y), asx_mod.ASX_HOST,
                            cfg=cfg, fetcher=fetcher)
        if page is None:
            continue
        parsed, _bad = await asyncio.to_thread(
            asx_mod.parse_asx_listing,
            page.content[: asx_mod.MAX_LISTING_BYTES].decode("utf-8", "replace"),
        )
        rows.extend(parsed)
    rows = list({r["ids_id"]: r for r in rows}.values())
    own = [
        r for r in rows
        if _HIGH_VALUE_HEADLINE.search(r.get("headline") or "")
        and not _THIRD_PARTY_HEADLINE.search(r.get("headline") or "")
    ]
    own.sort(key=lambda r: (_headline_rank(r["headline"]), -r["published_at"].timestamp()))
    docs: list[Any] = []
    for row in own[:MAX_DEEP_DOCS]:
        display = asx_mod.ASX_DISPLAY_URL.format(ids_id=row["ids_id"])
        shown = await _fetch(display, asx_mod.ASX_HOST, cfg=cfg, fetcher=fetcher)
        if shown is None:
            continue
        pdf_url = asx_mod.pdf_url_from_display_page(shown.content.decode("utf-8", "replace"))
        if not pdf_url:
            continue
        pdf = await _fetch(pdf_url, asx_mod.ASX_PDF_HOST, cfg=cfg, fetcher=fetcher)
        if pdf is None:
            continue
        text, body = await _pdf_deep_bundle(pdf.content)
        if text:
            docs.append(DocText(
                ref=str(row["ids_id"]), url=display, source_class="exchange_announcement",
                text=text, names_issuer=_issuer_named(text, entity.name, code, asx=True),
                full_text=body, content_hash=hashlib.sha256(pdf.content).hexdigest(),
                headline=str(row.get("headline") or "")[:200],
                published_at=row["published_at"].date().isoformat()
                if row.get("published_at") else None))
    return docs


async def _sec_documents(cik: str, data: dict[str, Any], entity: Any, *, cfg: Any,
                         fetcher: Any) -> list[Any]:
    """The registrant's latest annual and current reports, which state its website."""
    from app.services.discovery.domain_proofs import DocText

    recent = ((data.get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    prims = recent.get("primaryDocument") or []
    picked: list[tuple[str, str, str]] = []
    for wanted in (("10-K", "20-F", "40-F"), ("8-K", "10-Q", "6-K")):
        for form, acc, prim in zip(forms, accs, prims, strict=False):
            if form in wanted and re.fullmatch(r"[A-Za-z0-9._-]+\.(?:htm|html)", str(prim)) \
                    and re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(acc)):
                picked.append((form, str(acc), str(prim)))
                break
    docs: list[Any] = []
    for form, acc, prim in picked[:MAX_SEC_DOCS]:
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{prim}"
        result = await _fetch(url, "www.sec.gov", cfg=cfg, fetcher=fetcher)
        if result is None:
            continue
        text, body = await asyncio.to_thread(_filing_texts, result.content)
        # A filing under this CIK is the registrant speaking about itself.
        docs.append(DocText(ref=acc, url=url, source_class="regulator_filing", text=text,
                            names_issuer=True, full_text=body,
                            content_hash=hashlib.sha256(result.content).hexdigest(),
                            headline=form))
    return docs


async def _uk_documents(entity: Any, *, cfg: Any, fetcher: Any) -> list[Any]:
    """The issuer's recent FCA National Storage Mechanism filings, found by its GLEIF LEI."""
    import uuid

    from app.services.discovery.domain_proofs import DocText
    from app.services.sources.disclosures import uk_nsm
    from app.services.sources.disclosures.model import SOURCE_UK_FCA_NSM, VerifiedIssuer
    from app.services.sources.document_fetcher import safe_post_json

    if not entity.lei:
        return []
    try:
        result = await safe_post_json(
            uk_nsm.NSM_SEARCH_URL, uk_nsm._query(entity.lei),
            allowed_domains=(uk_nsm.NSM_SEARCH_HOST,), cfg=cfg)
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        payload = json.loads(content) if content else {}
    except Exception:  # noqa: BLE001
        return []
    from datetime import datetime, timezone

    issuer = VerifiedIssuer(
        company_id=uuid.uuid4(), ticker=entity.ticker or "", venue="LSE", name=entity.name,
        source_id=SOURCE_UK_FCA_NSM, identity_basis="GLEIF LEI for the exchange ISIN",
        isin=entity.isin, lei=entity.lei)
    documents, _refused = uk_nsm.parse_nsm_hits(
        payload, issuer, lookback_days=730, now=datetime.now(timezone.utc))
    documents.sort(key=lambda d: (_headline_rank(d.headline), -d.published_at.timestamp()))
    docs: list[Any] = []
    for doc in documents[:MAX_UK_DOCS]:
        got = await _fetch(doc.official_url, uk_nsm.NSM_ARTEFACT_HOST, cfg=cfg, fetcher=fetcher)
        if got is None:
            continue
        if doc.media == "pdf":
            text, body = await _pdf_deep_bundle(got.content)
        else:
            text = await asyncio.to_thread(_html_text, got.content)
            body = text
        if text:
            docs.append(DocText(ref=doc.document_ref, url=doc.official_url,
                                source_class="regulator_filing", text=text,
                                names_issuer=_issuer_named(text, entity.name, entity.ticker),
                                full_text=body,
                                content_hash=hashlib.sha256(got.content).hexdigest(),
                                headline=str(doc.headline or "")[:200],
                                published_at=doc.published_at.date().isoformat()))
    return docs


def _from_proof(proof: Any) -> OfficialDomain:
    return OfficialDomain(
        proof.domain, proof.method, proof.source_url, proof.detail, proof.verified_at,
        method=proof.method, source_class=proof.source_class, document_id=proof.document_id,
        matched_entity=proof.matched_entity, matched_identifiers=dict(proof.matched_identifiers),
        confidence=proof.confidence, status=proof.status,
    )


_DOCS: dict[tuple[str, str, str], list[Any]] = {}
MAX_DOCS_CACHED = 24  # full texts are large: far fewer entries than the small caches


async def _collect_docs(
    venue: str, ticker: str, entity: Any, cik: str | None, sec_data: dict[str, Any] | None,
    *, cfg: Any, fetcher: Any,
) -> list[Any]:
    """The issuer's authoritative documents, fetched once a day and shared by the domain proofs
    and Path C (theme passages), so one document is never fetched or parsed twice."""
    key = (venue.upper(), ticker.upper(), _today())
    if key in _DOCS:
        return list(_DOCS[key])
    docs: list[Any] = []
    v = venue.upper()
    if v in ("AU", "ASX") and _safe_code(ticker):
        docs = await _asx_documents(_safe_code(ticker) or "", entity, cfg=cfg, fetcher=fetcher)
    elif v == "US" and cik and sec_data is not None:
        docs = await _sec_documents(str(cik), sec_data, entity, cfg=cfg, fetcher=fetcher)
    elif v == "LSE":
        docs = await _uk_documents(entity, cfg=cfg, fetcher=fetcher)
    if docs:
        _DOCS.pop(key, None)
        _DOCS[key] = docs
        while len(_DOCS) > MAX_DOCS_CACHED:
            _DOCS.pop(next(iter(_DOCS)))
    return list(docs)


async def regulated_documents(
    *, ticker: str | None, venue: str | None, name: str, cik: str | None = None,
    cfg: Any = None, fetcher: Any = None,
) -> tuple[Any, list[Any]]:
    """``(entity, documents)`` for Path C. **Never raises.** Empty for a venue with no
    authoritative document source (the caller records why)."""
    try:
        entity = await resolve_entity(ticker=ticker, venue=venue, name=name, cik=cik,
                                      cfg=cfg, fetcher=fetcher)
        sec_data = None
        if (venue or "").upper() == "US" and cik:
            sec_data = await _sec_submissions(str(cik), cfg=cfg, fetcher=fetcher)
        docs = await asyncio.wait_for(
            _collect_docs((venue or ""), ticker or "", entity, cik, sec_data,
                          cfg=cfg, fetcher=fetcher),
            RESOLVER_WALL_SECONDS)
        return entity, docs
    except Exception:  # noqa: BLE001 - no document is no evidence, never an error
        return None, []


async def _document_proofs(
    venue: str, ticker: str, entity: Any, cik: str | None, sec_data: dict[str, Any] | None,
    *, cfg: Any, fetcher: Any,
) -> list[OfficialDomain]:
    from app.services.discovery.domain_proofs import proofs_from_documents

    docs = await _collect_docs(venue, ticker, entity, cik, sec_data, cfg=cfg, fetcher=fetcher)
    from app.services.discovery.entity_match import _distinctive, _tokens, relations_from_document

    toks = _distinctive(_tokens(entity.name))
    relations: list[Any] = []
    for doc in docs:
        if doc.names_issuer:
            relations.extend(relations_from_document(
                doc.full_text or doc.text, name_tokens=toks, document_id=doc.ref, url=doc.url,
                source_class=doc.source_class))
    if relations:
        _bounded_put(_RELATIONS, (venue.upper(), ticker.upper()), relations[:12])
    # Only VERIFIED proofs are an issuer's official domain; a single unconfirmed statement is
    # not (it would carry the A3 issuer voice on one document's word).
    return [
        _from_proof(p)
        for p in proofs_from_documents(docs, entity, verified_at=_today())
        if p.status == "verified_official"
    ]


async def establish_official_domains(
    *,
    ticker: str | None,
    venue: str | None,
    verified: bool,
    cik: str | None = None,
    cfg: Any = None,
    fetcher: Any = None,
    name: str | None = None,
) -> list[OfficialDomain]:
    """The issuer's official domains, from independent sources only. **Never raises.**

    ``verified`` must be True: a company whose LISTING the platform has not confirmed
    through an official source gets no official domain, whatever a page says about itself.
    """
    if not verified or not ticker:
        return []
    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    key = ((venue or "").upper(), (ticker or "").upper(), cik or "", _today())
    if key in _CACHE:
        return list(_CACHE[key])
    found: list[OfficialDomain] = []
    registry = get_verified_issuer_source(ticker, venue)
    if registry is not None:
        for domain in (registry.official_website_domain, *registry.allowed_domains):
            d = registrable_domain((domain or "").lower().removeprefix("www."))
            if d and not any(o.domain == d for o in found):
                found.append(OfficialDomain(d, BASIS_REGISTRY, None,
                                            "the platform's verified issuer registry", _today()))
    sec_data: dict[str, Any] | None = None
    try:
        if (venue or "").upper() in ("AU", "ASX"):
            code = _safe_code(ticker)
            if code:
                found.extend(await _asx(code, cfg=cfg, fetcher=fetcher))
        elif (venue or "").upper() == "US" and cik:
            sec_data = await _sec_submissions(str(cik), cfg=cfg, fetcher=fetcher)
            found.extend(await _sec(str(cik), cfg=cfg, fetcher=fetcher, data=sec_data))
    except Exception:  # noqa: BLE001 - a lookup that fails leaves the domain unestablished
        pass
    # Nothing from the exchange profile or the latest announcements: read the issuer's own
    # high-value regulated documents (bounded). The domain is proven by those BYTES, matched to
    # the verified entity — never by a search snippet.
    if not found and name:
        try:
            entity = await resolve_entity(ticker=ticker, venue=venue, name=name, cik=cik,
                                          cfg=cfg, fetcher=fetcher)
            found.extend(await asyncio.wait_for(
                _document_proofs((venue or ""), ticker or "", entity, cik, sec_data,
                                 cfg=cfg, fetcher=fetcher),
                RESOLVER_WALL_SECONDS))
        except Exception:  # noqa: BLE001 - an unreadable document names no domain
            pass
    unique: list[OfficialDomain] = []
    for item in found:
        if not any(u.domain == item.domain for u in unique):
            unique.append(item)
    # An empty answer is never remembered: a transient failure must not blind the lookup for
    # the rest of the process, and a source that publishes later should be read then.
    if unique:
        _bounded_put(_CACHE, key, unique)
    return list(unique)


__all__ = [
    "BASIS_EXCHANGE_ANNOUNCEMENT",
    "BASIS_EXCHANGE_PROFILE",
    "BASIS_REGISTRY",
    "BASIS_REGULATOR_PROFILE",
    "OfficialDomain",
    "resolve_entity",
    "domain_covers",
    "domains_in_letterhead",
    "is_issuer_voice",
    "establish_official_domains",
    "reset_cache",
]
