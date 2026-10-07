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
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from app.services.discovery.identity import EXCHANGE_HOSTS, registrable_domain

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
    "automicgroup.com.au", "boardroomlimited.com.au", "advancedshare.com.au",
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
_BARE_DOMAIN = re.compile(
    r"(?<![@\w./-])([a-z0-9][a-z0-9-]{1,}(?:\.[a-z0-9-]{2,})*\."
    r"(?:com\.au|co\.uk|co\.nz|com|net|org|io|au|uk|ca|eu|de|fr|se|no|dk|fi|nl|ch|it|es))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OfficialDomain:
    """One independently established official domain of one issuer."""

    domain: str
    basis: str
    source_url: str | None = None
    detail: str | None = None
    as_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_never_an_issuer(domain: str) -> bool:
    d = domain.lower()
    return any(d == bad or d.endswith("." + bad) for bad in _NEVER_AN_ISSUER)


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


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #

_CACHE: dict[tuple[str, str, str], list[OfficialDomain]] = {}


def reset_cache() -> None:
    _CACHE.clear()


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
    small = sorted(
        (i for i in items if isinstance(i, dict) and i.get("documentKey")),
        key=lambda i: _file_size_kb(i.get("fileSize")),
    )[:MAX_ANNOUNCEMENTS]
    seen: dict[str, str] = {}
    for item in small:
        key = str(item["documentKey"])
        if not re.fullmatch(r"[A-Za-z0-9-]{6,60}", key):
            continue
        file_url = f"https://{ASX_FILE_HOST}/apiman-gateway/ASX/asx-research/1.0/file/{key}"
        doc = await _fetch(file_url, ASX_FILE_HOST, cfg=cfg, fetcher=fetcher)
        if doc is None:
            continue
        text = await asyncio.to_thread(_pdf_first_page_text, doc.content)
        domains = domains_in_letterhead(text)
        if len(domains) > MAX_LETTERHEAD_DOMAINS:
            continue  # a letterhead naming several sites is ambiguous: take none of them
        for domain in domains:
            seen.setdefault(domain, file_url)
        if seen:
            break  # one exchange-published letterhead is enough; do not spend more fetches
    for domain, url in seen.items():
        out.append(OfficialDomain(
            domain, BASIS_EXCHANGE_ANNOUNCEMENT, url,
            "printed in the letterhead of an announcement the exchange published under "
            f"the ticker {code}", _today(),
        ))
    return out


async def _sec(cik: str, *, cfg: Any, fetcher: Any) -> list[OfficialDomain]:
    if not re.fullmatch(r"\d{1,10}", str(cik or "")):
        return []
    url = f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"
    result = await _fetch(url, SEC_DATA_HOST, cfg=cfg, fetcher=fetcher, json_body=True)
    if result is None:
        return []
    try:
        data = json.loads(result.content.decode("utf-8", "replace"))
    except ValueError:
        return []
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


async def establish_official_domains(
    *,
    ticker: str | None,
    venue: str | None,
    verified: bool,
    cik: str | None = None,
    cfg: Any = None,
    fetcher: Any = None,
) -> list[OfficialDomain]:
    """The issuer's official domains, from independent sources only. **Never raises.**

    ``verified`` must be True: a company whose LISTING the platform has not confirmed
    through an official source gets no official domain, whatever a page says about itself.
    """
    if not verified or not ticker:
        return []
    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    key = ((venue or "").upper(), (ticker or "").upper(), cik or "")
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
    try:
        if (venue or "").upper() in ("AU", "ASX"):
            code = _safe_code(ticker)
            if code:
                found.extend(await _asx(code, cfg=cfg, fetcher=fetcher))
        elif (venue or "").upper() == "US" and cik:
            found.extend(await _sec(str(cik), cfg=cfg, fetcher=fetcher))
    except Exception:  # noqa: BLE001 - a lookup that fails leaves the domain unestablished
        pass
    unique: list[OfficialDomain] = []
    for item in found:
        if not any(u.domain == item.domain for u in unique):
            unique.append(item)
    _CACHE[key] = unique
    return list(unique)


__all__ = [
    "BASIS_EXCHANGE_ANNOUNCEMENT",
    "BASIS_EXCHANGE_PROFILE",
    "BASIS_REGISTRY",
    "BASIS_REGULATOR_PROFILE",
    "OfficialDomain",
    "domain_covers",
    "domains_in_letterhead",
    "establish_official_domains",
    "reset_cache",
]
