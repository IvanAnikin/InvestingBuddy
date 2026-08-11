"""
Bounded, allowlisted, SSRF-safe web fetcher — Phase 29B.1.

Fetches a single company-owned page (an issuer's investor-relations,
annual-reports or newsroom landing page) and returns its title, meta description
and a bounded set of extracted links. It exists to turn a *verified, code-defined*
issuer URL into real primary-company evidence — it is deliberately NOT a general
web fetcher.

Safety properties (why this is not an SSRF surface):
  * **No arbitrary URL.** A URL only ever reaches this module from the
    code-defined ``verified_issuer_sources`` registry, or from a link extracted
    from an already-allowlisted page — and every URL is re-checked against the
    issuer's ``allowed_domains`` before a request is made. The API never accepts
    a URL.
  * **HTTPS only.** ``http://`` and every other scheme is rejected.
  * **Host allowlist.** The host must be inside the issuer's ``allowed_domains``.
  * **No private / internal targets.** IP-literal hosts and localhost / .internal
    / .local style names are rejected (belt-and-braces on top of the allowlist).
  * **Redirects are guarded, not followed blindly.** A redirect to a host outside
    the allowlist, or a downgrade to http, aborts the fetch (honest "blocked"
    result); at most a few same-allowlist hops are followed.
  * **Bounded.** Timeout + max-bytes + max-links are all config-capped; a page
    larger than the cap is truncated, never fully buffered.
  * **Never raises.** Every failure (timeout, 4xx/5xx, blocked, parse error)
    degrades to a ``SafeFetchResult`` with ``error`` / ``blocked`` set.
  * **Secret-free.** Nothing here logs prompts, bodies, or credentials; result
    URLs are stripped of any query secrets by the caller's ``EvidenceItem``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.redaction import strip_url_secrets
from app.services.sources.verified_issuer_sources import (
    host_of,
    registrable_host_allowed,
)

_USER_AGENT = (
    "InvestingBuddy-Research-Bot/1.0 (+internal research; contact: "
    "research@investingbuddy.example)"
)

# Hostnames that must never be fetched even if (mis)configured into an allowlist.
_INTERNAL_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")
_INTERNAL_HOST_EXACT = frozenset(
    {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
)

# Cloud instance-metadata endpoints — never a legitimate fetch target. The IPv4
# address is inside link-local (169.254.0.0/16) so it is already rejected by the
# is_link_local check below; it is enumerated here for explicitness + the IPv6
# form, which is what the resolved-IP guard reports on.
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

# A resolver is any callable shaped like ``socket.getaddrinfo`` — injectable so
# the DNS guard can be unit-tested without touching real DNS.
Resolver = Callable[..., list[Any]]

# Link text keywords that mark an annual-report / financial-disclosure link.
# Phase 32A Problem B: widened with generic (never issuer-specific) current-
# results vocabulary — the original set covered annual/full-year and
# "half-year report"/"interim report" *document* phrasing, but missed the
# common "half-year RESULTS" / "H1 results" / "financial results" / "results
# release" phrasing many issuers use for their current-period results pages
# (the proven LVMH gap: the index page was fetched but its current-results
# link never matched any keyword).
ANNUAL_REPORT_KEYWORDS: tuple[str, ...] = (
    "annual report",
    "universal registration document",
    "registration document",
    "integrated report",
    "financial report",
    "annual results",
    "full-year results",
    "full year results",
    "annual financial report",
    "results presentation",
    "half-year report",
    "half year report",
    "interim report",
    "half-year results",
    "half year results",
    "first-half results",
    "first half results",
    "h1 results",
    "interim results",
    "financial results",
    "results release",
    "quarterly results",
)
# Only used when no annual/financial report link is found on the page.
FALLBACK_REPORT_KEYWORDS: tuple[str, ...] = ("sustainability report", "esg report")

# Link text keywords that mark a press / news release link.
PRESS_KEYWORDS: tuple[str, ...] = (
    "press release",
    "press-release",
    "news release",
    "announcement",
    "ad hoc",
    "ad-hoc",
    "regulatory news",
    "media release",
)

# File-extension hint that a link is a downloadable report document.
_DOC_EXTENSIONS = (".pdf",)


@dataclass(frozen=True)
class SafeLink:
    """One bounded, allowlisted link extracted from a fetched page."""

    url: str
    text: str
    is_document: bool = False


@dataclass
class SafeFetchResult:
    """Everything one bounded page fetch produced. Never carries a secret."""

    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    links: list[SafeLink] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False
    # Phase 32A Slice 5B.1 — the ALREADY byte-capped page body, kept so a caller
    # can run the richer, non-browser discovery strategies (JSON-LD, hydration
    # state, embedded script JSON) over it. An <a href>-only scan finds nothing on
    # a JS-rendered IR page, which is why Slice 5A discovered 0 documents for five
    # of seven issuers. Never logged, never persisted, never sent to a model.
    body_html: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.blocked and (self.status_code or 0) < 400


# --------------------------------------------------------------------------- #
# URL / host guards (pure, network-free — unit-tested without any I/O)
# --------------------------------------------------------------------------- #


def is_safe_public_host(host: str | None) -> bool:
    """False for localhost / private / link-local / internal / IP-literal hosts."""
    if not host:
        return False
    h = host.strip().lower().rstrip(".")
    if not h or h in _INTERNAL_HOST_EXACT:
        return False
    if any(h.endswith(sfx) for sfx in _INTERNAL_HOST_SUFFIXES):
        return False
    # Reject IP-literal hosts outright (companies are reached by domain name);
    # this closes the private/loopback/link-local/metadata-endpoint vectors.
    stripped = h.strip("[]")
    try:
        ip = ipaddress.ip_address(stripped)
    except ValueError:
        ip = None
    if ip is not None:
        return False
    return True


def _ip_is_public(ip_text: str) -> bool:
    """True when ``ip_text`` is a routable, public unicast address.

    False for loopback / private / link-local / reserved / multicast /
    unspecified addresses — i.e. every SSRF-relevant internal range.
    """
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_resolved_ip_public(
    host: str | None,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> str | None:
    """Return None if EVERY resolved IP for ``host`` is public, else a reason.

    Closes the DNS-rebinding / name-that-resolves-internal SSRF vector that a
    hostname allowlist alone cannot: it resolves ``host`` and rejects the target
    if ANY resolved address is loopback / private / link-local / reserved /
    multicast, or the cloud instance-metadata endpoint (``169.254.169.254``).
    ``resolver`` is injectable (shaped like ``socket.getaddrinfo``) so this is
    unit-testable without real DNS. Never raises — a resolution error is itself a
    "block" reason.
    """
    if not host:
        return "empty host"
    try:
        infos = resolver(host, None)
    except Exception as exc:  # noqa: BLE001 - a resolution failure is a block
        return f"dns resolution failed: {type(exc).__name__}"
    ips: list[str] = []
    for info in infos or []:
        try:
            sockaddr = info[4]
            if sockaddr:
                ips.append(str(sockaddr[0]))
        except (IndexError, TypeError):
            continue
    if not ips:
        return "no resolved ip"
    for ip_text in ips:
        clean = ip_text.split("%", 1)[0]  # drop any IPv6 scope id
        if clean in _METADATA_IPS:
            return f"resolved to metadata ip: {clean}"
        if not _ip_is_public(clean):
            return f"resolved to non-public ip: {clean}"
    return None


def looks_like_pdf(raw: bytes) -> bool:
    """True when ``raw`` begins with the ``%PDF-`` magic-byte signature.

    A cheap, reusable content-sniff so a caller never feeds a non-PDF blob (an
    HTML error page served as ``application/octet-stream``, say) to a PDF parser.
    """
    return bool(raw) and raw[:5] == b"%PDF-"


def check_fetch_url(
    url: str | None,
    allowed_domains: tuple[str, ...],
    *,
    cfg: Settings | None = None,
    resolve_ip: bool = False,
    resolver: Resolver = socket.getaddrinfo,
) -> str | None:
    """Return None if ``url`` is safe to fetch, else a short reason string.

    A URL is safe only when: it parses; scheme is https; host is a safe public
    host; and host is inside ``allowed_domains``. When ``allowlist_only`` is off
    (never in production), the allowlist check is skipped but every other guard
    still applies.

    When ``resolve_ip`` is True the host is additionally DNS-resolved and every
    resolved IP must be public (see :func:`assert_resolved_ip_public`) — this is
    OPT-IN and defaults OFF so all existing callers are byte-for-byte unchanged.
    ``resolver`` is injectable for tests.
    """
    cfg = cfg or default_settings
    if not url:
        return "empty url"
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return "unparseable url"
    if parts.scheme != "https":
        return f"non-https scheme: {parts.scheme or 'none'}"
    host = (parts.hostname or "").lower()
    if not is_safe_public_host(host):
        return f"unsafe/internal host: {host or 'none'}"
    if cfg.source_connector_allowlist_only and not registrable_host_allowed(
        host, allowed_domains
    ):
        return f"host not in allowlist: {host}"
    if resolve_ip:
        dns_reason = assert_resolved_ip_public(host, resolver=resolver)
        if dns_reason:
            return f"unsafe resolved ip ({dns_reason})"
    return None


async def async_check_fetch_url(
    url: str | None,
    allowed_domains: tuple[str, ...],
    *,
    cfg: Settings | None = None,
    resolve_ip: bool = False,
    resolver: Resolver = socket.getaddrinfo,
) -> tuple[str | None, str | None]:
    """Async twin of :func:`check_fetch_url` returning ``(reason, pinned_ip)``.

    Applies exactly the same guards, but resolves off the event loop and hands
    back the validated address so the caller can PIN the connection to it
    (Phase 32A Slice 5B.1 — closes the ADR-014 rebinding window). ``pinned_ip`` is
    None whenever ``resolve_ip`` is False, which is the default, so a caller that
    does not opt in behaves exactly as before.

    An explicitly injected ``resolver`` is honoured (the Slice 5A test seam);
    left at the default the lookup goes through ``loop.getaddrinfo`` instead of
    blocking the worker on a synchronous ``socket.getaddrinfo``.
    """
    reason = check_fetch_url(url, allowed_domains, cfg=cfg)
    if reason:
        return reason, None
    if not resolve_ip:
        return None, None

    from app.services.sources.pinned_transport import resolve_and_validate

    host = (urlsplit(url or "").hostname or "").lower()
    injected = None if resolver is socket.getaddrinfo else resolver
    ip, dns_reason = await resolve_and_validate(host, resolver=injected)
    if dns_reason:
        return f"unsafe resolved ip ({dns_reason})", None
    return None, ip


def pinned_transport_for(
    cfg: Settings, host: str | None, ip: str | None
) -> Any | None:
    """Build a transport pinned to ``ip`` for ``host``, or None.

    None means "connect normally": either pinning is switched off by config, or
    no address was validated (``resolve_ip`` off), or this httpx build cannot
    support it. None is never a claim that pinning happened — callers that care
    record the degradation.
    """
    if not ip or not host:
        return None
    if not getattr(cfg, "primary_document_pin_dns_enabled", True):
        return None
    from app.services.sources.pinned_transport import build_pinned_transport

    return build_pinned_transport({host: ip})


# --------------------------------------------------------------------------- #
# HTML parsing (pure, network-free)
# --------------------------------------------------------------------------- #


class _PageParser(HTMLParser):
    """Extracts <title>, <meta name=description>, <html lang> and <a href> links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta_description: str | None = None
        self.lang: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        # (href, accumulated_text)
        self.anchors: list[tuple[str, str]] = []
        self._cur_href: str | None = None
        self._cur_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "html" and not self.lang and a.get("lang"):
            self.lang = a["lang"].strip() or None
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.meta_description:
                self.meta_description = (a.get("content") or "").strip() or None
        elif tag == "a" and a.get("href"):
            self._cur_href = a["href"].strip()
            self._cur_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            if not self.title:
                self.title = " ".join(self._title_parts).strip() or None
        elif tag == "a" and self._cur_href is not None:
            self.anchors.append((self._cur_href, " ".join(self._cur_text).strip()))
            self._cur_href = None
            self._cur_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._cur_href is not None:
            self._cur_text.append(data)


def _parse_html(html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - a malformed page must never raise
        pass
    return parser


def parse_title(html: str) -> str | None:
    return _parse_html(html).title


def parse_meta_description(html: str) -> str | None:
    return _parse_html(html).meta_description


def _link_matches(text: str, href: str, keywords: tuple[str, ...]) -> bool:
    hay = f"{text} {href}".lower()
    return any(kw in hay for kw in keywords)


def extract_links(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...],
    max_links: int,
    fallback_keywords: tuple[str, ...] = (),
) -> list[SafeLink]:
    """Extract bounded, allowlisted links whose text/href matches ``keywords``.

    Absolute-resolves each href against ``base_url``, keeps only HTTPS links on an
    allowlisted host, de-dups by URL, and caps the count. If nothing matches the
    primary keywords, ``fallback_keywords`` are tried (e.g. sustainability report
    only when no annual report link exists).
    """
    parser = _parse_html(html)
    primary = _collect_links(
        parser.anchors, base_url, allowed_domains, keywords, max_links
    )
    if primary or not fallback_keywords:
        return primary
    return _collect_links(
        parser.anchors, base_url, allowed_domains, fallback_keywords, max_links
    )


def _collect_links(
    anchors: list[tuple[str, str]],
    base_url: str,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...],
    max_links: int,
) -> list[SafeLink]:
    out: list[SafeLink] = []
    seen: set[str] = set()
    for href, text in anchors:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if not _link_matches(text, href, keywords):
            continue
        absolute = strip_url_secrets(urljoin(base_url, href)) or ""
        if not absolute.startswith("https://"):
            continue
        host = host_of(absolute)
        if not is_safe_public_host(host) or not registrable_host_allowed(
            host, allowed_domains
        ):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        is_doc = absolute.lower().split("?")[0].endswith(_DOC_EXTENSIONS)
        out.append(SafeLink(url=absolute, text=text[:200], is_document=is_doc))
        if len(out) >= max_links:
            break
    return out


# --------------------------------------------------------------------------- #
# The bounded fetch (real network — used ONLY by the live preview path)
# --------------------------------------------------------------------------- #


async def safe_fetch_page(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
    fallback_keywords: tuple[str, ...] = (),
    cfg: Settings | None = None,
    resolve_ip: bool = False,
    resolver: Resolver = socket.getaddrinfo,
) -> SafeFetchResult:
    """Fetch one allowlisted HTTPS page (bounded, guarded, never raising).

    ``resolve_ip`` is OPT-IN (default OFF): when True the target host's resolved
    IPs are checked before the initial fetch AND after each redirect hop. Left OFF
    every existing caller is byte-for-byte unchanged.
    """
    cfg = cfg or default_settings
    result = SafeFetchResult(requested_url=url)

    reason, pinned_ip = await async_check_fetch_url(
        url, allowed_domains, cfg=cfg, resolve_ip=resolve_ip, resolver=resolver
    )
    if reason:
        result.blocked = True
        result.error = reason
        return result

    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        result.error = f"http client unavailable: {type(exc).__name__}"
        return result

    max_bytes = max(1, cfg.source_connector_max_bytes)
    timeout = max(1, cfg.source_connector_timeout_seconds)
    current = url
    # When an address was validated, connect ONLY to it: the name is never
    # resolved a second time, so it cannot rebind between check and connect.
    transport = pinned_transport_for(cfg, host_of(url), pinned_ip)
    client_kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": timeout,
        "headers": {"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
    }
    if transport is not None:
        client_kwargs["transport"] = transport
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            for _hop in range(4):  # bounded redirect chain
                async with client.stream("GET", current) as resp:
                    result.status_code = resp.status_code
                    result.final_url = current
                    if resp.is_redirect:
                        location = resp.headers.get("location", "")
                        nxt = urljoin(current, location)
                        block, next_ip = await async_check_fetch_url(
                            nxt,
                            allowed_domains,
                            cfg=cfg,
                            resolve_ip=resolve_ip,
                            resolver=resolver,
                        )
                        if block:
                            result.blocked = True
                            result.error = f"redirect blocked ({block})"
                            return result
                        # Re-pin: the new hop gets its own validated address; the
                        # previous hop's pin is never reused for a new host.
                        if transport is not None and next_ip:
                            transport.pin(host_of(nxt), next_ip)
                        current = nxt
                        continue
                    if resp.status_code >= 400:
                        result.error = f"http {resp.status_code}"
                        return result
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= max_bytes:
                            break
                    body = b"".join(chunks)[:max_bytes].decode("utf-8", "replace")
                    result.body_html = body
                    parser = _parse_html(body)
                    result.title = parser.title
                    result.meta_description = parser.meta_description
                    result.links = extract_links(
                        body,
                        base_url=current,
                        allowed_domains=allowed_domains,
                        keywords=keywords,
                        max_links=cfg.source_connector_max_links_per_page,
                        fallback_keywords=fallback_keywords,
                    )
                    return result
            result.blocked = True
            result.error = "too many redirects"
            return result
    except Exception as exc:  # noqa: BLE001 - fetch must never crash a run
        result.error = f"fetch failed: {type(exc).__name__}"
        return result


__all__ = [
    "SafeLink",
    "SafeFetchResult",
    "Resolver",
    "is_safe_public_host",
    "assert_resolved_ip_public",
    "looks_like_pdf",
    "check_fetch_url",
    "async_check_fetch_url",
    "pinned_transport_for",
    "parse_title",
    "parse_meta_description",
    "extract_links",
    "safe_fetch_page",
    "ANNUAL_REPORT_KEYWORDS",
    "FALLBACK_REPORT_KEYWORDS",
    "PRESS_KEYWORDS",
]
