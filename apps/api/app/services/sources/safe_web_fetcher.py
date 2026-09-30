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
  * **HTTPS only, port 443 only, no userinfo** (W0 / D4, D5). Every other scheme is
    rejected; so are URLs over 2048 characters, with whitespace, control characters
    or backslashes, and URLs that ``urllib.parse`` and ``httpx.URL`` parse
    differently (host, port or userinfo).
  * **Host allowlist.** The host must be inside the issuer's ``allowed_domains``.
  * **No private / internal targets.** The host is IDNA-normalised (D10); IP literals
    in any encoding (decimal, hex, octal, short-form: D3), single-label hosts,
    localhost / .internal / .local style names, Azure platform hosts and this
    platform's own apps are rejected. Every RESOLVED address must be ``is_global``
    and outside an explicit denylist (CGNAT, all of 169.254/16, Azure WireServer
    ``168.63.129.16`` …: D1, D2, D13), with IPv4-mapped / 6to4 / Teredo / NAT64
    addresses unwrapped and re-checked. A fetch not bounded by the allowlist always
    resolves and pins.
  * **Client hygiene** (D6, D7, D8, D14). ``trust_env=False``; no cookie jar and no
    ``Cookie``/``Authorization`` on any hop; ``Accept-Encoding: identity`` with a
    decoded-bytes and ratio cap for a server that compresses anyway; and one total
    wall-clock deadline across every hop and the body.
  * **Redirects are guarded, not followed blindly.** A redirect to a host outside
    the allowlist, or a downgrade to http, aborts the fetch (honest "blocked"
    result); at most a few same-allowlist hops are followed.
  * **Bounded.** Timeout + max-bytes + max-links are all config-capped; a page
    larger than the cap is truncated, never fully buffered.
  * **Never raises.** Every failure (timeout, 4xx/5xx, blocked, parse error)
    degrades to a ``SafeFetchResult`` with ``error`` / ``blocked`` set.
  * **Secret-free.** Nothing here logs prompts, bodies, or credentials; stored
    link URLs are stripped of query secrets, while the URL actually requested is
    the link as published (``SafeLink.fetch_target``; D12).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import sys
import unicodedata
import zlib
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

#: The product token the User-Agent above starts with. robots.txt is evaluated against
#: exactly this, so a site that names us gets the behaviour it asked for (W0: the
#: traversal used to evaluate a DIFFERENT token than the one it sent).
USER_AGENT_PRODUCT_TOKEN = "InvestingBuddy-Research-Bot"

# Hostnames that must never be fetched even if (mis)configured into an allowlist.
_INTERNAL_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".localdomain",
    ".lan",
    ".home.arpa",
    # Azure platform hosts that hold this platform's own data or control planes
    # (W0, threat model §2.4 check 3). Never a research source.
    ".scm.azurewebsites.net",
    ".vault.azure.net",
    ".database.azure.com",
    ".blob.core.windows.net",
    ".internal.cloudapp.net",
)
_INTERNAL_HOST_EXACT = frozenset(
    {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
)
#: This platform's own App Service apps (``ib-stg-api``, ``ib-stg-web`` …). Fetching
#: ourselves is never research and is exactly what an SSRF wants.
_OWN_APP_HOST_PREFIX = "ib-"
_OWN_APP_HOST_SUFFIX = ".azurewebsites.net"

# Cloud instance-metadata endpoints — never a legitimate fetch target. The IPv4
# address is inside link-local (169.254.0.0/16) so it is already rejected by the
# denylist below; it is enumerated here for explicitness + the IPv6 form, which is
# what the resolved-IP guard reports on.
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

#: Explicit address denylist (W0 / threat model §2.4 check 4). Applied ON TOP of
#: ``is_global`` so a stdlib classification bug (CVE-2024-4032, D13) or a range the
#: stdlib calls global (CGNAT is not global, but Azure WireServer ``168.63.129.16`` is)
#: cannot open a path to an internal target.
_DENY_V4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT (D1)
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local incl. IMDS + App Service identity (D2)
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "168.63.129.16/32",  # Azure WireServer / platform DNS — a PUBLIC address (D2)
    )
)
_DENY_V6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
        "100::/64",
        "2001:db8::/32",
        "3fff::/20",
        # Local-use NAT64 (RFC 8215): the embedded IPv4 position depends on the
        # operator's prefix length, so it cannot be unwrapped reliably. Refused.
        "64:ff9b:1::/48",
    )
)
#: Well-known NAT64 prefix (RFC 6052): the low 32 bits are the IPv4 destination.
_NAT64_WKP = ipaddress.IPv6Network("64:ff9b::/96")

#: The oldest runtime whose ``ipaddress`` classification is trusted (CVE-2024-4032, D13).
MIN_SAFE_PYTHON: tuple[int, int, int] = (3, 12, 4)

#: URLs longer than this are refused before parsing continues (threat model §2.4 check 2).
MAX_URL_LENGTH = 2048

#: Decompressed bytes may be at most this multiple of the bytes received (D7) once the
#: body is past ``_RATIO_FLOOR_BYTES`` — a real HTML page compresses ~5-10x, a PDF ~1-2x.
MAX_DECOMPRESSION_RATIO = 100
_RATIO_FLOOR_BYTES = 1_000_000

_HOST_CHARS_RE = re.compile(r"^[a-z0-9._-]+$")
_NUMERIC_LABEL_RE = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")

# A resolver is any callable shaped like ``socket.getaddrinfo`` — injectable so
# the DNS guard can be unit-tested without touching real DNS.
Resolver = Callable[..., list[Any]]

_log = logging.getLogger(__name__)

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
# Link text keywords that mark a CURRENT-PERIOD (quarterly / part-year)
# publication — private-use readiness, current-period acceptance.
#
# ``ANNUAL_REPORT_KEYWORDS`` already covers half-year and "interim" wording, but
# it has no vocabulary at all for the quarterly SALES release many European
# issuers publish between their interim and annual reports. Live-observed:
# Richemont's ``…-fy27-q1-sales-en.pdf`` (the quarter ended 30 June 2026, its
# newest reporting) matched no keyword, so the current-period reserve had
# nothing newer than the FY26 interim report to reserve a slot for.
#
# Deliberately narrow: period vocabulary only. Adding general press-release
# wording here would pull a boutique opening or a product launch into the same
# bounded candidate cap as the issuer's own financial reporting.
CURRENT_PERIOD_KEYWORDS: tuple[str, ...] = (
    "q1 sales",
    "q2 sales",
    "q3 sales",
    "q4 sales",
    "quarterly sales",
    "quarterly report",
    "quarterly statement",
    "quarterly financial report",
    "first quarter",
    "second quarter",
    "third quarter",
    "fourth quarter",
    "nine months",
    "nine-month",
    "interim management statement",
    "trading update",
    "trading statement",
    "sales announcement",
    "sales release",
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
    #: The URL exactly as the page linked it, set ONLY when it differs from ``url``
    #: (W0 / D12). ``url`` is the stored/logged form, with credential-bearing query
    #: parameters stripped; stripping must never change what is actually fetched, so a
    #: caller that requests the link uses :attr:`fetch_target`. Excluded from equality
    #: and from ``repr`` so it can never leak into a log line or a comparison.
    fetch_url: str = field(default="", compare=False, repr=False)

    @property
    def fetch_target(self) -> str:
        """The URL to request: the unmodified link when stripping changed it."""
        return self.fetch_url or self.url


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


def python_runtime_is_safe(version_info: Any = None) -> bool:
    """True when the running interpreter's ``ipaddress`` classification is trusted.

    CVE-2024-4032 (D13): before 3.12.4 ``is_private`` / ``is_global`` were wrong for
    several ranges. The explicit denylist below does not depend on them, but the
    ``is_global`` requirement does, so an older runtime refuses open-web fetches.
    """
    raw = tuple(version_info if version_info is not None else sys.version_info)[:3]
    try:
        current = tuple(int(part) for part in raw)
    except (TypeError, ValueError):
        return False
    return current >= MIN_SAFE_PYTHON


def open_web_fetch_refusal(version_info: Any = None) -> str | None:
    """A coded refusal when open-web fetching must not run on this runtime, else None."""
    if python_runtime_is_safe(version_info):
        return None
    return (
        "python runtime below "
        + ".".join(str(p) for p in MIN_SAFE_PYTHON)
        + ": open-web fetch refused (CVE-2024-4032)"
    )


def log_python_runtime_check() -> bool:
    """Log (never raise) whether this runtime may perform open-web fetches.

    Called at startup. A too-old runtime does not crash the app: allowlisted fetches
    keep working on the explicit denylist, and open-web fetches are refused by
    :func:`open_web_fetch_refusal`.
    """
    safe = python_runtime_is_safe()
    if not safe:
        _log.warning(
            "python_runtime_below_safe_minimum version=%s minimum=%s open_web_fetch=refused",
            ".".join(str(p) for p in tuple(sys.version_info)[:3]),
            ".".join(str(p) for p in MIN_SAFE_PYTHON),
        )
    return safe


def _has_forbidden_char(text: str) -> bool:
    """Whitespace, C0/DEL control characters and backslashes (threat model §2.4)."""
    return any(
        ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in text
    )


def normalize_host(host: str | None) -> str | None:
    """The host as lower-case A-labels (IDNA / UTS-46), or None when it cannot be.

    Every host check runs on this form (D10), so a Unicode host and its punycode are
    one host, not two. A trailing root dot is dropped. IP literals are returned as
    text for the caller to classify (and refuse).
    """
    if not host:
        return None
    h = host.strip()
    if not h or _has_forbidden_char(h):
        return None
    h = h.rstrip(".")
    if not h:
        return None
    if h.isascii():
        return h.lower()
    try:
        import idna  # httpx's own dependency: no new package

        return str(idna.encode(h, uts46=True).decode("ascii")).lower()
    except Exception:  # noqa: BLE001 - an unencodable host is not a host we fetch
        try:
            return h.encode("idna").decode("ascii").lower()
        except Exception:  # noqa: BLE001
            return None


def _label_scripts(label: str) -> set[str]:
    scripts: set[str] = set()
    for ch in label:
        if not ch.isalpha():
            continue
        try:
            scripts.add(unicodedata.name(ch).split(" ", 1)[0])
        except ValueError:
            scripts.add("UNKNOWN")
    return scripts


def mixed_script_host(host: str | None) -> bool:
    """True when any label of ``host`` mixes writing systems (a homograph signal).

    ``аpple.com`` with a Cyrillic ``а`` is the textbook case. This is a SIGNAL for
    reputation scoring (W0 exposes it; later slices consume it), not a refusal: a
    legitimate Japanese label mixes Kanji and Kana. A whole-script lookalike (every
    letter Cyrillic) is not detected here.
    """
    normalized = normalize_host(host)
    if not normalized:
        return False
    for label in normalized.split("."):
        text = label
        if label.startswith("xn--"):
            try:
                import idna

                text = str(idna.decode(label))
            except Exception:  # noqa: BLE001
                try:
                    text = label.encode("ascii").decode("idna")
                except Exception:  # noqa: BLE001 - undecodable punycode is itself odd
                    return True
        if len(_label_scripts(text)) > 1:
            return True
    return False


def _is_own_app_host(host: str) -> bool:
    if host.endswith(_OWN_APP_HOST_SUFFIX) and host.startswith(_OWN_APP_HOST_PREFIX):
        return True
    own = (os.environ.get("WEBSITE_HOSTNAME") or "").strip().lower().rstrip(".")
    return bool(own) and host == own


def is_safe_public_host(host: str | None) -> bool:
    """False for localhost / private / internal / IP-literal / encoded-IP hosts.

    W0 hardening (threat model §2.4 check 3): the host is IDNA-normalised first; IP
    literals in any encoding are refused — including the forms a URL parser or
    ``inet_aton`` reads as IPv4 (``2130706433``, ``0x7f000001``, ``0177.0.0.1``,
    ``127.1``: D3); single-label hosts, empty labels and anything outside
    ``[a-z0-9._-]`` are refused; so are Azure platform hosts and this platform's own
    apps.
    """
    h = normalize_host(host)
    if not h:
        return False
    try:
        ipaddress.ip_address(h.strip("[]").split("%", 1)[0])
    except ValueError:
        pass
    else:
        return False
    if len(h) > 253 or not _HOST_CHARS_RE.match(h):
        return False
    if h in _INTERNAL_HOST_EXACT:
        return False
    if any(h.endswith(sfx) for sfx in _INTERNAL_HOST_SUFFIXES):
        return False
    if _is_own_app_host(h):
        return False
    labels = h.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    # A host whose last label is numeric (decimal or 0x-hex) is parsed as IPv4 by the
    # WHATWG URL standard and by inet_aton; no real TLD is numeric.
    if _NUMERIC_LABEL_RE.match(labels[-1]) or all(
        _NUMERIC_LABEL_RE.match(label) for label in labels
    ):
        return False
    return True


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses an IPv6 address carries: mapped, 6to4, Teredo and NAT64."""
    out: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        out.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        out.append(ip.sixtofour)
    if ip.teredo is not None:
        out.extend(ip.teredo)
    if ip in _NAT64_WKP:
        out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return out


def _address_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv4Address):
        return bool(ip.is_global) and not any(ip in net for net in _DENY_V4_NETWORKS)
    if ip.ipv4_mapped is not None:
        # ::ffff:a.b.c.d IS a.b.c.d; classify what it points at.
        return _address_is_public(ip.ipv4_mapped)
    if any(ip in net for net in _DENY_V6_NETWORKS):
        return False
    if not ip.is_global:
        return False
    return all(_address_is_public(v4) for v4 in _embedded_ipv4(ip))


def _ip_is_public(ip_text: str) -> bool:
    """True when ``ip_text`` is a globally routable address outside the denylist.

    W0 (D1, D2, D13): requires ``is_global`` AND no hit in the explicit denylist, and
    unwraps IPv4-mapped / 6to4 / Teredo / NAT64 addresses to re-check the embedded
    IPv4 address. CGNAT (``100.64/10``) and Azure WireServer (``168.63.129.16``) are
    therefore refused, which the old ``is_private``-based test let through.
    """
    try:
        ip = ipaddress.ip_address(str(ip_text).split("%", 1)[0])
    except ValueError:
        return False
    return _address_is_public(ip)


def assert_resolved_ip_public(
    host: str | None,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> str | None:
    """Return None if EVERY resolved IP for ``host`` is public, else a reason.

    Closes the DNS-rebinding / name-that-resolves-internal SSRF vector that a
    hostname allowlist alone cannot: it resolves ``host`` and rejects the target
    if ANY resolved address is not public (see :func:`_ip_is_public`), or is the
    cloud instance-metadata endpoint (``169.254.169.254``).
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


def check_url_shape(url: str | None) -> tuple[str | None, str | None]:
    """Return ``(reason, normalised_host)`` for the URL's shape alone. Network-free.

    Threat model §2.4 checks 1-3, before any host policy: length, forbidden
    characters, ``https`` only, no userinfo (D4), port absent or 443 (D5), and parser
    agreement — the URL is parsed by ``urllib.parse`` AND ``httpx.URL``, and any
    disagreement about host, port or userinfo is a refusal (the classic
    ``https://a.example\\@127.0.0.1/`` differential).
    """
    if not url or not isinstance(url, str):
        return "empty url", None
    if len(url) > MAX_URL_LENGTH:
        return f"unsafe url: longer than {MAX_URL_LENGTH} characters", None
    if _has_forbidden_char(url):
        return "unsafe url: whitespace, control character or backslash", None
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return "unparseable url", None
    if parts.scheme != "https":
        return f"non-https scheme: {parts.scheme or 'none'}", None
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return "unsafe url: userinfo is not allowed", None
    try:
        port = parts.port
    except ValueError:
        return "unsafe url: invalid port", None
    if port not in (None, 443):
        return f"unsafe url: port {port} is not allowed", None
    host = normalize_host(parts.hostname)
    if not host:
        return f"unsafe/internal host: {parts.hostname or 'none'}", None
    try:
        import httpx
    except Exception:  # noqa: BLE001 - no second parser available; the first stands
        return None, host
    try:
        other = httpx.URL(url)
    except Exception:  # noqa: BLE001 - one parser refusing is a disagreement
        return "unparseable url", None
    other_host = (other.raw_host or b"").decode("ascii", "replace").lower().rstrip(".")
    other_port = None if other.port in (None, 443) else other.port
    if (
        other.scheme != "https"
        or other.userinfo
        or other_host.strip("[]") != host.strip("[]")
        or other_port is not None
    ):
        return "unsafe url: parsers disagree on host, port or userinfo", None
    return None, host


def _static_fetch_reason(
    url: str | None, allowed_domains: tuple[str, ...], cfg: Settings
) -> tuple[str | None, str | None]:
    """Every network-free check, returning ``(reason, normalised_host)``."""
    reason, host = check_url_shape(url)
    if reason:
        return reason, None
    if not is_safe_public_host(host):
        return f"unsafe/internal host: {host or 'none'}", None
    if cfg.source_connector_allowlist_only and not registrable_host_allowed(
        host, allowed_domains
    ):
        return f"host not in allowlist: {host}", None
    return None, host


def _must_resolve(cfg: Settings, resolve_ip: bool) -> bool:
    """W0 / D3: a fetch the allowlist does not bound ALWAYS resolves and checks."""
    return bool(resolve_ip) or not cfg.source_connector_allowlist_only


def check_fetch_url(
    url: str | None,
    allowed_domains: tuple[str, ...],
    *,
    cfg: Settings | None = None,
    resolve_ip: bool = False,
    resolver: Resolver = socket.getaddrinfo,
) -> str | None:
    """Return None if ``url`` is safe to fetch, else a short reason string.

    A URL is safe only when: its shape passes :func:`check_url_shape`; its host
    is a safe public host; and the host is inside ``allowed_domains``. When
    ``allowlist_only`` is off (never in production), the allowlist check is skipped
    but every other guard still applies — and the resolved-address check becomes
    mandatory, because nothing else then bounds where the URL may point (W0 / D3).

    When ``resolve_ip`` is True the host is additionally DNS-resolved and every
    resolved IP must be public (see :func:`assert_resolved_ip_public`).
    ``resolver`` is injectable for tests.
    """
    cfg = cfg or default_settings
    reason, host = _static_fetch_reason(url, allowed_domains, cfg)
    if reason:
        return reason
    if _must_resolve(cfg, resolve_ip):
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
    None whenever resolution was not requested. W0: a fetch that is not bounded by
    the allowlist (``source_connector_allowlist_only`` off) always resolves and pins.

    An explicitly injected ``resolver`` is honoured (the Slice 5A test seam);
    left at the default the lookup goes through ``loop.getaddrinfo`` instead of
    blocking the worker on a synchronous ``socket.getaddrinfo``.
    """
    cfg = cfg or default_settings
    reason, host = _static_fetch_reason(url, allowed_domains, cfg)
    if reason:
        return reason, None
    if not _must_resolve(cfg, resolve_ip):
        return None, None

    from app.services.sources.pinned_transport import resolve_and_validate

    injected = None if resolver is socket.getaddrinfo else resolver
    ip, dns_reason = await resolve_and_validate(host, resolver=injected)
    if dns_reason:
        return f"unsafe resolved ip ({dns_reason})", None
    return None, ip


# --------------------------------------------------------------------------- #
# Guarded client + bounded body (W0: D6, D7, D8, D14)
# --------------------------------------------------------------------------- #

BODY_DEADLINE_EXCEEDED = "total deadline exceeded"
BODY_DECOMPRESSION_RATIO = "decompression ratio exceeded"
BODY_UNSUPPORTED_ENCODING = "unsupported content-encoding"
BODY_UNDECODABLE = "undecodable content-encoding"


def _no_cookie_jar() -> Any:
    """A cookie jar whose policy accepts and returns nothing (D14).

    httpx keeps one jar per client and one client serves every redirect hop, so a
    ``Set-Cookie`` from hop 1 would otherwise be replayed to hop 2 — possibly a
    different host.
    """
    from http.cookiejar import CookieJar, DefaultCookiePolicy

    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


async def _strip_credential_headers(request: Any) -> None:
    """Per-hop request hook: no ``Cookie`` and no ``Authorization``, ever."""
    for name in ("cookie", "authorization", "proxy-authorization"):
        try:
            request.headers.pop(name, None)
        except Exception:  # noqa: BLE001 - a header object without pop has none to strip
            continue


def guarded_client_kwargs(
    *,
    timeout: float,
    headers: dict[str, str],
    transport: Any | None = None,
) -> dict[str, Any]:
    """The ``httpx.AsyncClient`` kwargs every guarded fetch uses.

    * ``follow_redirects=False`` — every hop is re-checked by the caller;
    * ``trust_env=False`` — no environment proxy may reroute a fetch (D6);
    * a no-op cookie jar and a per-hop hook stripping ``Cookie``/``Authorization`` (D14);
    * ``Accept-Encoding: identity`` — the byte cap then measures what is held (D7).
    """
    kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": timeout,
        "trust_env": False,
        "cookies": _no_cookie_jar(),
        "event_hooks": {"request": [_strip_credential_headers], "response": []},
        "headers": {**headers, "Accept-Encoding": "identity"},
    }
    if transport is not None:
        kwargs["transport"] = transport
    return kwargs


def fetch_total_deadline_seconds(cfg: Settings) -> float:
    """The whole-fetch wall-clock budget (D8): connect + every hop + the body."""
    return max(0.05, float(cfg.source_fetch_total_deadline_seconds))


@dataclass
class BoundedBody:
    """What :func:`read_bounded_body` read. ``error`` is a code, never provider text."""

    content: bytes = b""
    truncated: bool = False
    error: str | None = None


async def read_bounded_body(
    resp: Any,
    *,
    max_bytes: int,
    deadline: float | None = None,
    max_ratio: int = MAX_DECOMPRESSION_RATIO,
) -> BoundedBody:
    """Read at most ``max_bytes`` DECODED bytes from a streaming response.

    * The cap applies to decoded bytes and never overshoots: the last chunk is sliced
      to what remains (D7).
    * An encoded body (``gzip``/``deflate``, sent despite ``Accept-Encoding:
      identity``) is decoded incrementally from the raw stream with a bounded output
      per step, so one small compressed chunk cannot inflate past the cap in memory;
      past ``_RATIO_FLOOR_BYTES`` the decoded:received ratio is capped at
      ``max_ratio`` and a body over it is refused as a decompression bomb.
    * Any other content-encoding is refused rather than handed to a decoder we
      cannot bound.
    * ``deadline`` (a ``loop.time()`` value) is checked on every chunk (D8).
    """
    loop = asyncio.get_running_loop()
    headers = getattr(resp, "headers", None) or {}
    encoding = str(headers.get("content-encoding") or "").strip().lower()
    chunks: list[bytes] = []
    total = 0

    def _late() -> bool:
        return deadline is not None and loop.time() > deadline

    if encoding in ("", "identity"):
        async for chunk in resp.aiter_bytes():
            if _late():
                return BoundedBody(b"".join(chunks), error=BODY_DEADLINE_EXCEEDED)
            remaining = max_bytes - total
            if len(chunk) >= remaining:
                chunks.append(chunk[:remaining])
                return BoundedBody(b"".join(chunks), truncated=True)
            chunks.append(chunk)
            total += len(chunk)
        return BoundedBody(b"".join(chunks))

    if encoding not in ("gzip", "x-gzip", "deflate") or not hasattr(resp, "aiter_raw"):
        return BoundedBody(error=BODY_UNSUPPORTED_ENCODING)

    wbits = 16 + zlib.MAX_WBITS if encoding != "deflate" else zlib.MAX_WBITS
    decoder = zlib.decompressobj(wbits)
    tried_raw_deflate = False
    received = 0
    async for raw in resp.aiter_raw():
        if _late():
            return BoundedBody(b"".join(chunks), error=BODY_DEADLINE_EXCEEDED)
        received += len(raw)
        buf = raw
        while buf:
            ratio_ceiling = max(_RATIO_FLOOR_BYTES, received * max_ratio)
            limit = min(max_bytes, ratio_ceiling)
            allowed = limit - total
            if allowed <= 0:
                if ratio_ceiling < max_bytes:
                    return BoundedBody(error=BODY_DECOMPRESSION_RATIO)
                return BoundedBody(b"".join(chunks), truncated=True)
            try:
                out = decoder.decompress(buf, allowed + 1)
            except zlib.error:
                if encoding == "deflate" and total == 0 and not tried_raw_deflate:
                    # Many servers send raw DEFLATE without the zlib wrapper.
                    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                    tried_raw_deflate = True
                    continue
                return BoundedBody(b"".join(chunks), error=BODY_UNDECODABLE)
            buf = decoder.unconsumed_tail
            if len(out) > allowed:
                if ratio_ceiling < max_bytes:
                    return BoundedBody(error=BODY_DECOMPRESSION_RATIO)
                chunks.append(out[:allowed])
                return BoundedBody(b"".join(chunks), truncated=True)
            chunks.append(out)
            total += len(out)
            if decoder.eof:
                break
    return BoundedBody(b"".join(chunks))


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
        # W0 / D12: secrets are stripped from the STORED form only. The URL that
        # is later requested is the link exactly as published — a stripped URL is a
        # different resource (a signed CDN link without its signature, say).
        raw = urljoin(base_url, href)
        absolute = strip_url_secrets(raw) or ""
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
        out.append(
            SafeLink(
                url=absolute,
                text=text[:200],
                is_document=is_doc,
                fetch_url=raw if raw != absolute else "",
            )
        )
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

    ``resolve_ip`` is OPT-IN (default OFF) for allowlisted fetches: when True the
    target host's resolved IPs are checked before the initial fetch AND after each
    redirect hop, and the connection is pinned. A fetch the allowlist does not bound
    always resolves (W0 / D3).

    W0 hardening: no environment proxy, no cookie carried between hops, identity
    encoding with a decoded-bytes + ratio cap, and a total wall-clock deadline
    (``source_fetch_total_deadline_seconds``) across every hop and the body.
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
    budget = fetch_total_deadline_seconds(cfg)
    deadline = asyncio.get_running_loop().time() + budget
    current = url
    # When an address was validated, connect ONLY to it: the name is never
    # resolved a second time, so it cannot rebind between check and connect.
    transport = pinned_transport_for(cfg, host_of(url), pinned_ip)
    client_kwargs = guarded_client_kwargs(
        timeout=timeout,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
        transport=transport,
    )
    try:
        async with asyncio.timeout(budget):
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
                            # Re-pin: the new hop gets its own validated address;
                            # the previous hop's pin is never reused for a new host.
                            if transport is not None and next_ip:
                                transport.pin(host_of(nxt), next_ip)
                            current = nxt
                            continue
                        if resp.status_code >= 400:
                            result.error = f"http {resp.status_code}"
                            return result
                        read = await read_bounded_body(
                            resp, max_bytes=max_bytes, deadline=deadline
                        )
                        if read.error:
                            result.blocked = read.error != BODY_DEADLINE_EXCEEDED
                            result.error = f"fetch failed: {read.error}"
                            return result
                        body = read.content.decode("utf-8", "replace")
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
    except TimeoutError:
        result.error = f"fetch failed: {BODY_DEADLINE_EXCEEDED}"
        return result
    except Exception as exc:  # noqa: BLE001 - fetch must never crash a run
        result.error = f"fetch failed: {type(exc).__name__}"
        return result


__all__ = [
    "SafeLink",
    "SafeFetchResult",
    "Resolver",
    "BoundedBody",
    "MAX_DECOMPRESSION_RATIO",
    "MAX_URL_LENGTH",
    "MIN_SAFE_PYTHON",
    "USER_AGENT_PRODUCT_TOKEN",
    "check_url_shape",
    "fetch_total_deadline_seconds",
    "guarded_client_kwargs",
    "is_safe_public_host",
    "log_python_runtime_check",
    "mixed_script_host",
    "normalize_host",
    "open_web_fetch_refusal",
    "python_runtime_is_safe",
    "read_bounded_body",
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
    "CURRENT_PERIOD_KEYWORDS",
    "FALLBACK_REPORT_KEYWORDS",
    "PRESS_KEYWORDS",
]
