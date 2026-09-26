"""Identity and listing verification: a lead becomes an issuer only on evidence — V3.19.4.

THE RULE (spec §5.3)
====================
A company a search provider named is a CLAIM. It may enter a discovery universe only when
the platform, fetching a page ITSELF (the guarded fetcher: HTTPS, public IP, no off-host
redirect, byte cap), finds the company's name AND its ticker (or a checksum-valid ISIN)
on a page published by:

* an EXCHANGE operator (a closed list of hosts below),
* a REGULATOR or official filing system (``publisher_tiers`` T1/T2), or
* the ISSUER itself — the page's registrable domain label equals a distinctive token of
  the company's name, by equality, so a lookalike domain cannot pass.

A search snippet, an aggregator page or model text never verifies a listing. An aggregator
may DISCOVER a company — it is recorded as where the lead came from — and that is all.

The venue must also be one ``exchange_registry`` knows, so the listing's country, region
and currency are known facts rather than guesses.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.services.discovery.leads import CompanyLead
from app.services.exchange_registry import (
    country_for_exchange,
    currency_for_exchange,
    get_exchange,
    region_for_exchange,
)

IDENTITY_VERIFIED = "verified"
IDENTITY_PLATFORM = "platform_registry"
IDENTITY_REJECTED = "rejected"

REJECT_UNKNOWN_VENUE = "unknown_venue"
REJECT_NO_TICKER = "no_ticker"
REJECT_NO_LISTING_EVIDENCE = "no_listing_evidence"
REJECT_NAME_MISMATCH = "name_mismatch"
REJECT_VENUE_UNVERIFIED = "venue_unverified"
REJECT_FETCH_FAILED = "fetch_failed"
REJECT_DUPLICATE = "duplicate"
REJECT_BUDGET = "verification_budget_exhausted"

SOURCE_EXCHANGE = "exchange"
SOURCE_REGULATOR = "regulator"
SOURCE_ISSUER = "issuer"

#: Exchange operators' registrable domains. A page here IS the exchange speaking.
EXCHANGE_HOSTS: tuple[str, ...] = (
    "euronext.com", "six-group.com", "londonstockexchange.com", "lseg.com",
    "borsaitaliana.it", "nasdaqomxnordic.com", "nasdaq.com", "nyse.com", "asx.com.au",
    "tmx.com", "tsx.com", "tmxmoney.com", "boerse-frankfurt.de", "deutsche-boerse.com",
    "xetra.com", "bolsamadrid.es", "bmex.es", "oslobors.no", "wienerborse.at", "gpw.pl",
    "jpx.co.jp", "hkex.com.hk", "nzx.com", "jse.co.za", "otcmarkets.com", "cboe.com",
    "cse.lk", "thecse.com", "aquis.eu", "spotlightstockmarket.com", "ngm.se",
    "euronext.dk", "nasdaq.dk", "firstnorth.com",
)

#: A provider writes venues as names; the registry knows codes. Long names → codes.
VENUE_ALIASES: dict[str, str] = {
    "euronext paris": "PA", "paris": "PA", "epa": "PA", "xpar": "PA",
    "euronext amsterdam": "AS", "amsterdam": "AS", "xams": "AS",
    "euronext brussels": "BR", "brussels": "BR",
    "euronext milan": "MI", "borsa italiana": "MI", "milan": "MI", "euronext growth milan": "MI",
    "bit": "MI", "mta": "MI", "xmil": "MI",
    "euronext lisbon": "LS", "lisbon": "LS", "euronext dublin": "IR", "dublin": "IR",
    "euronext oslo": "OL", "oslo børs": "OL", "oslo bors": "OL", "oslo": "OL",
    "euronext growth oslo": "OL",
    "six swiss exchange": "SW", "six": "SW", "swx": "SW", "swiss exchange": "SW",
    "london stock exchange": "LSE", "lse": "LSE", "london stock exchange aim": "LSE",
    "aim": "LSE", "lon": "LSE", "london": "LSE", "xlon": "LSE",
    "nasdaq copenhagen": "CO", "copenhagen": "CO", "cph": "CO", "xcse": "CO",
    "nasdaq stockholm": "ST", "stockholm": "ST", "sto": "ST", "first north": "ST",
    "nasdaq first north": "ST", "nasdaq helsinki": "HE", "helsinki": "HE",
    "xetra": "XETRA", "frankfurt stock exchange": "F", "frankfurt": "F", "fra": "F",
    "deutsche börse": "XETRA", "deutsche borse": "XETRA", "etr": "XETRA",
    "bolsa de madrid": "MC", "madrid": "MC", "bme": "MC",
    "vienna stock exchange": "VI", "wiener börse": "VI", "warsaw stock exchange": "WA",
    "asx": "AU", "australian securities exchange": "AU",
    "tsx": "TO", "toronto stock exchange": "TO", "tsx venture": "V",
    "tsx venture exchange": "V", "tsxv": "V", "cse": None,  # type: ignore[dict-item]
    "nasdaq": "US", "nyse": "US", "nyse american": "US", "nasdaq capital market": "US",
    "nasdaq global select": "US", "nasdaq global market": "US", "nyse arca": "US",
    "otc": "OTC", "otcqx": "OTC", "otcqb": "OTC", "otc markets": "OTC", "otc pink": "OTC",
    "lse aim": "LSE", "london aim": "LSE", "euronext growth paris": "PA",
    "euronext growth": "PA", "nasdaq copenhagen a/s": "CO", "johannesburg": "JSE",
    "tsx-v": "V", "cboe canada": "NEO",
    "tokyo stock exchange": "TSE", "hong kong stock exchange": "HK", "hkex": "HK",
    "nzx": "NZ", "johannesburg stock exchange": "JSE", "jse": "JSE",
}

_LEGAL_SUFFIXES = {
    "sa", "s.a.", "spa", "s.p.a.", "ag", "plc", "a/s", "as", "asa", "ab", "ltd",
    "limited", "inc", "inc.", "corp", "corp.", "corporation", "nv", "n.v.", "se",
    "sca", "s.c.a.", "group", "holding", "holdings", "company", "co", "co.", "the",
    "gmbh", "kgaa", "oyj", "publ", "(publ)", "bhd", "llc", "lp", "pty", "sas",
}

_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")


def _canonical_code(code: str | None) -> str | None:
    """One code per venue family for dedup: every SEC-eligible US venue is ``US``."""
    info = get_exchange(code) if code else None
    if info is None:
        return None
    if info.is_us and info.sec_eligible:
        return "US"
    return info.code


def normalise_venue(raw: str | None) -> str | None:
    """A registry venue code for a provider's venue string, or None when unknown.

    Tried in order: the whole string, the string with parenthesised parts removed, each
    parenthesised part, then comma/colon/semicolon-separated parts. A hyphen is never a
    separator ("TSX-V" is the Venture exchange, not "TSX").
    """
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw).strip())
    candidates = [text]
    bare = re.sub(r"\([^)]*\)", " ", text).strip()
    candidates.append(re.sub(r"\s+", " ", bare))
    candidates.extend(m.strip() for m in re.findall(r"\(([^)]*)\)", text))
    candidates.extend(part.strip() for part in re.split(r"[,:;/|]", bare))
    for candidate in candidates:
        if not candidate:
            continue
        low = candidate.lower()
        if low in VENUE_ALIASES:
            if VENUE_ALIASES[low]:
                return _canonical_code(VENUE_ALIASES[low]) or VENUE_ALIASES[low]
            continue
        found = _canonical_code(candidate.upper())
        if found is not None:
            return found
    return None


#: The words a page uses for each venue. A listing is verified only when the page names
#: the company, its ticker AND its venue — a provider saying "Frankfurt" for a NYSE
#: company is refused, not turned into a German listing.
def venue_words(code: str | None) -> list[str]:
    info = get_exchange(code) if code else None
    words = {alias for alias, target in VENUE_ALIASES.items()
             if target and (_canonical_code(target) or target) == code and len(alias) >= 3}
    if info is not None:
        words.add(info.name.lower())
    if code == "US":
        words.update({"nasdaq", "nyse", "new york stock exchange", "nyse american"})
    return sorted(w for w in words if w)


def name_tokens(name: str | None) -> list[str]:
    """Distinctive tokens of a company name: legal suffixes and short words dropped."""
    words = re.findall(r"[a-z0-9&]+", _fold(name))
    return [w for w in words if w not in _LEGAL_SUFFIXES and len(w) >= 3]


def _fold(text: str | None) -> str:
    """Lower-case, accents folded: "Hermès" → "hermes", "Ørsted" → "orsted"."""
    import unicodedata

    raw = (text or "").lower().replace("ø", "o").replace("æ", "ae").replace("ß", "ss")
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(ch)
    )


def registrable_label(host: str | None) -> str | None:
    """The label before the public suffix: ``ir.kering.com`` → ``kering``."""
    if not host:
        return None
    parts = host.lower().strip(".").split(".")
    if len(parts) < 2:
        return None
    # Two-level public suffixes this platform meets (co.uk, com.au, co.jp …).
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac"}:
        return parts[-3]
    return parts[-2]


def registrable_domain(host: str | None) -> str | None:
    if not host:
        return None
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else None


#: Words too generic to identify an issuer by domain: "Global Mining Ltd" must not make
#: mining.com (a news site) its own website.
_GENERIC_TOKENS = frozenset({
    "mining", "mines", "minerals", "mineral", "metals", "metal", "resources", "resource",
    "gold", "silver", "copper", "lithium", "uranium", "nickel", "energy", "power", "global",
    "international", "capital", "industries", "industrial", "technologies", "technology",
    "tech", "materials", "material", "rare", "earth", "earths", "critical", "strategic",
    "luxury", "fashion", "brands", "brand", "group", "royal", "american", "european",
    "first", "new", "united", "national", "general", "exploration", "ventures", "partners",
    "investments", "finance", "financial", "bank", "trust", "fund", "holdings", "world",
    "news", "market", "markets", "stock", "stocks", "invest", "investor", "investors",
})
#: TLDs an issuer's own site plausibly uses: generic corporate ones and the country codes
#: of the venues the platform knows. ``kering.xyz`` or ``kering.io`` is not Kering's site.
_ISSUER_TLDS = frozenset({
    "com", "net", "org", "group", "global",
    "fr", "it", "de", "ch", "dk", "se", "no", "fi", "uk", "au", "ca", "us", "nl", "be",
    "es", "at", "ie", "pt", "pl", "lu", "jp", "hk", "nz", "za", "mx", "br", "cn", "kr",
    "tw", "in", "sg",
})


def issuer_domain_matches(host: str | None, company_name: str | None) -> bool:
    """True when the host is plausibly the ISSUER's own site. By equality, never similarity.

    The registrable label must equal the company's distinctive name — the concatenation
    of its distinctive tokens, or its first token when that token is itself distinctive
    (``kering`` for Kering SA, ``mpmaterials`` for MP Materials, ``lynasrareearths`` or
    ``lynas`` for Lynas Rare Earths) — never a generic word, and only on a corporate or
    venue-country TLD.
    """
    label = registrable_label(host)
    domain = registrable_domain(host)
    if not label or not domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if tld not in _ISSUER_TLDS:
        return False
    label = label.replace("-", "")
    tokens = name_tokens(company_name)
    if not tokens:
        return False
    raw = [w for w in re.findall(r"[a-z0-9]+", _fold(company_name))
           if w not in _LEGAL_SUFFIXES]
    candidates = {"".join(tokens), "".join(raw)}
    if len(raw) >= 2:
        candidates.add("".join(raw[:2]))
    first = raw[0] if raw else tokens[0]
    # An acronym name ("BHP Group") is distinctive at three letters.
    acronym = bool(re.match(r"^[A-Z0-9]{3}\b", (company_name or "").strip()))
    if first not in _GENERIC_TOKENS and (len(first) >= 4 or acronym):
        candidates.add(first)
        # The issuer's own group/investor domain: pandoragroup.com, kering-group.com …
        for suffix in ("group", "corp", "inc", "plc", "holding", "holdings", "ir",
                       "investors", "global", "international", "company", "co"):
            candidates.add(first + suffix)
    minimum = 3 if acronym else 4
    candidates = {c for c in candidates if c and c not in _GENERIC_TOKENS and len(c) >= minimum}
    return label in candidates


def publisher_kind(url: str | None, company_name: str | None) -> str | None:
    """exchange | regulator | issuer | None (a host that cannot verify a listing)."""
    from app.services.sources.publisher_tiers import publisher_tier

    host = (urlsplit(url or "").hostname or "").lower()
    if not host:
        return None
    domain = registrable_domain(host) or host
    if any(domain == h or host.endswith("." + h) or host == h for h in EXCHANGE_HOSTS):
        return SOURCE_EXCHANGE
    tier = publisher_tier(url)
    if tier in ("T1_primary_filing", "T2_regulator_or_gov"):
        return SOURCE_REGULATOR
    if issuer_domain_matches(host, company_name):
        return SOURCE_ISSUER
    return None


def _isin_valid(isin: str) -> bool:
    from app.services.entities.identifiers import isin_checksum_valid

    try:
        return bool(isin_checksum_valid(isin))
    except Exception:  # noqa: BLE001
        return False


_TICKER_CONTEXT = re.compile(
    r"(?:ticker|symbol|code|stock|shares?|listed|listing|exchange|isin|mnemonic|epa|asx|tsx"
    r"|nasdaq|nyse|lse|six|euronext|xetra|otc)",
    re.IGNORECASE,
)


def page_evidences_listing(
    text: str, *, name: str, ticker: str | None, venue: str | None = None
) -> tuple[bool, str]:
    """Does this page name the company AND its ticker (or its ISIN)? ``(ok, basis)``.

    The ticker must appear as an upper-case token; a short one (≤3 characters: "MP",
    "AI", "ON") only counts beside a listing word ("ticker", "shares", a venue name …)
    so incidental words cannot pass. An ISIN counts only when it sits within 300
    characters of the company's name — an exchange list page carries hundreds of them.
    """
    if not text:
        return False, "the page has no readable text"
    low = _fold(text)
    tokens = name_tokens(name)
    named = [t for t in tokens if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low)]
    # Every distinctive token for a short name; at least two of three for a long one.
    needed = len(tokens) if len(tokens) <= 2 else max(2, (len(tokens) * 2 + 2) // 3)
    if not tokens or len(named) < needed:
        return False, f"the page does not name the company ({', '.join(tokens) or name})"
    if venue is not None:
        words = venue_words(venue)
        if words and not any(
            re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low) for w in words
        ):
            return False, f"the page does not name the claimed venue ({venue})"
    if ticker:
        symbol = re.escape(ticker.upper())
        for match in re.finditer(rf"(?<![A-Za-z0-9]){symbol}(?![A-Za-z0-9])", text):
            if len(ticker) > 3:
                return True, f"names the company and its ticker {ticker.upper()}"
            window = text[max(0, match.start() - 40) : match.end() + 40]
            if _TICKER_CONTEXT.search(window):
                return True, (
                    f"names the company and its ticker {ticker.upper()} in a listing context"
                )
    anchor = next(
        (m.start() for t in tokens[:1]
         for m in re.finditer(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low)),
        None,
    )
    for match in _ISIN_RE.finditer(text):
        if anchor is not None and abs(match.start() - anchor) <= 300 and _isin_valid(
            match.group(0)
        ):
            return True, f"names the company and, beside it, ISIN {match.group(0)}"
    return False, "the page names the company but not its ticker or its ISIN"


@dataclass
class IdentityOutcome:
    lead: CompanyLead
    status: str
    ticker: str | None = None
    exchange: str | None = None
    name: str | None = None
    listing_country: str | None = None
    listing_region: str | None = None
    listing_currency: str | None = None
    rejection_reason: str | None = None
    detail: str | None = None
    listing_source: dict[str, Any] | None = None
    company_id: str | None = None
    fetches: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status in (IDENTITY_VERIFIED, IDENTITY_PLATFORM)

    def identity_record(self) -> dict[str, Any]:
        return {
            "identity_status": self.status,
            "ticker": self.ticker,
            "exchange": self.exchange,
            "name": self.name,
            "listing_country": self.listing_country,
            "listing_region": self.listing_region,
            "listing_currency": self.listing_currency,
            "listing_source": self.listing_source,
            "rejection_reason": self.rejection_reason,
            "detail": self.detail,
            "company_id": self.company_id,
        }

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["lead"] = self.lead.to_dict()
        return out


def dedup_key(ticker: str | None, exchange: str | None) -> str | None:
    if not ticker or not exchange:
        return None
    code = _canonical_code(exchange) or exchange.upper()
    return f"{code}:{ticker.upper()}"


def normalised_name(name: str | None) -> str:
    """The FULL name minus legal suffixes, for dedup. Short words are kept: "AB Science"
    must not collapse to "science" and collide with another company."""
    words = re.findall(r"[a-z0-9&]+", _fold(name))
    return " ".join(w for w in words if w not in _LEGAL_SUFFIXES)


async def _page_text(url: str, cfg: Any, fetcher: Any) -> tuple[str | None, dict[str, Any]]:
    from app.services.providers.leads import _document_text
    from app.services.sources.document_fetcher import safe_fetch_document

    host = (urlsplit(url).hostname or "").lower()
    record: dict[str, Any] = {"url": url}
    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(host,), cfg=cfg, resolve_ip=True
        )
    except Exception as exc:  # noqa: BLE001 - a failed fetch is a rejected attempt
        record["error"] = type(exc).__name__
        return None, record
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        record["error"] = getattr(result, "error", None) or "no content"
        return None, record
    record["content_hash"] = hashlib.sha256(content).hexdigest()
    record["final_url"] = getattr(result, "final_url", None) or url
    try:
        text, _complete = await asyncio.to_thread(
            _document_text, content, getattr(result, "document_type", None) or "html", cfg
        )
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"unreadable ({type(exc).__name__})"
        return None, record
    return text, record


async def verify_identity(
    lead: CompanyLead,
    *,
    cfg: Any = None,
    fetcher: Any = None,
    max_fetches: int = 2,
) -> IdentityOutcome:
    """Verify one EXTERNAL lead's listing. Never raises; always names its reason."""
    exchange = normalise_venue(lead.exchange_raw)
    ticker = (lead.ticker or "").strip().upper() or None
    base: dict[str, Any] = {
        "lead": lead,
        "ticker": ticker,
        "exchange": exchange,
        "name": lead.name,
    }
    if exchange is None or get_exchange(exchange) is None:
        return IdentityOutcome(
            status=IDENTITY_REJECTED, rejection_reason=REJECT_UNKNOWN_VENUE,
            detail=f"venue {lead.exchange_raw!r} is not one the platform knows", **base,
        )
    if not ticker:
        return IdentityOutcome(
            status=IDENTITY_REJECTED, rejection_reason=REJECT_NO_TICKER,
            detail="the lead names no ticker", **base,
        )
    listing: dict[str, Any] = {
        "listing_country": country_for_exchange(exchange),
        "listing_region": region_for_exchange(exchange),
        "listing_currency": currency_for_exchange(exchange),
    }
    urls: list[tuple[str, str]] = []
    for url in (lead.listing_source_url, lead.evidence_url):
        if not url or any(u == url for u, _ in urls):
            continue
        kind = publisher_kind(url, lead.name)
        if kind is not None:
            urls.append((url, kind))
    if not urls:
        return IdentityOutcome(
            status=IDENTITY_REJECTED, rejection_reason=REJECT_NO_LISTING_EVIDENCE,
            detail=(
                "no cited page is on an exchange, regulator or the issuer's own site; an "
                "aggregator or article can discover a company, never verify its listing"
            ),
            **base, **listing,
        )
    attempts: list[dict[str, Any]] = []
    fetches = 0
    last_reason = REJECT_FETCH_FAILED
    last_detail = "no cited page could be fetched"
    for url, kind in urls[:max_fetches]:
        fetches += 1
        text, record = await _page_text(url, cfg, fetcher)
        record["publisher_kind"] = kind
        attempts.append(record)
        if text is None:
            continue
        ok, basis = page_evidences_listing(text, name=lead.name, ticker=ticker, venue=exchange)
        record["basis"] = basis
        if ok:
            return IdentityOutcome(
                status=IDENTITY_VERIFIED,
                listing_source={
                    "url": record.get("final_url") or url,
                    "tier": kind,
                    "content_hash": record.get("content_hash"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "basis": basis,
                },
                fetches=fetches,
                attempts=attempts,
                **base,
                **listing,
            )
        last_reason = (
            REJECT_NAME_MISMATCH if "does not name the company" in basis
            else REJECT_VENUE_UNVERIFIED if "claimed venue" in basis
            else REJECT_NO_LISTING_EVIDENCE
        )
        last_detail = basis
    return IdentityOutcome(
        status=IDENTITY_REJECTED, rejection_reason=last_reason, detail=last_detail,
        fetches=fetches, attempts=attempts, **base, **listing,
    )


def platform_identity(
    lead: CompanyLead, *, exchange: str | None, company_id: str | None = None
) -> IdentityOutcome:
    """A lead already on record (curated registry or a held company): T3 reference data."""
    code = normalise_venue(exchange) or exchange
    return IdentityOutcome(
        lead=lead,
        status=IDENTITY_PLATFORM,
        ticker=(lead.ticker or "").upper() or None,
        exchange=code,
        name=lead.name,
        listing_country=country_for_exchange(code) if code else None,
        listing_region=region_for_exchange(code) if code else None,
        listing_currency=currency_for_exchange(code) if code else None,
        listing_source={"url": None, "tier": "T3_curated_reference_list",
                        "registry": lead.source},
        company_id=company_id,
    )


__all__ = [
    "EXCHANGE_HOSTS",
    "IDENTITY_PLATFORM",
    "IDENTITY_REJECTED",
    "IDENTITY_VERIFIED",
    "IdentityOutcome",
    "dedup_key",
    "issuer_domain_matches",
    "name_tokens",
    "normalise_venue",
    "normalised_name",
    "page_evidences_listing",
    "platform_identity",
    "publisher_kind",
    "verify_identity",
]
