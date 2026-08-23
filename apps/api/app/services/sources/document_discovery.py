"""
Bounded, non-browser primary-document discovery — Phase 32A Slice 5B.1.

Slice 5A discovered issuer documents with ``<a href>`` anchors only. Modern
investor-relations pages (Burberry, Kering, LVMH, Hermès, BAE …) are JS-gated
single-page apps: their *served* HTML contains ZERO matching anchors, so
discovery returned 0 links for 5 of 7 issuers even though the official document
URLs are right there in the payload — inside ``application/ld+json`` blocks,
``__NEXT_DATA__`` / ``__INITIAL_STATE__`` hydration state, other embedded script
JSON, or a linked RSS / sitemap feed.

This module adds those strategies. It is **pure parsing of bytes somebody else
already fetched**:

  * **No network.** Nothing here imports ``httpx`` or opens a socket. Feed XML
    and JSON-endpoint bodies are fetched by the caller (through the existing
    allowlisted, SSRF-guarded ``safe_web_fetcher`` / ``document_fetcher``) and
    handed in as text.
  * **No headless browser, no crawler.** No JS is executed, no page is followed,
    no recursion into other pages. ``find_json_endpoints`` merely *reports*
    same-origin data URLs for the caller to decide about.
  * **HTTPS only.** ``http://`` and every other scheme is dropped.
  * **Host allowlist + no private/internal targets.** Every returned URL is
    re-checked with ``is_safe_public_host`` and ``registrable_host_allowed``
    against the issuer's ``allowed_domains`` — a URL found in a JSON blob is
    treated as exactly as untrusted as one found in an anchor.
  * **Secret-free.** Every URL is passed through ``canonicalize_source_url``, so
    credential-bearing query parameters AND ``user:pass@`` userinfo are removed
    before a candidate can become evidence; nothing here logs page bodies,
    prompts or credentials.
  * **XXE / entity-expansion safe.** Feed XML containing ``<!DOCTYPE`` or
    ``<!ENTITY`` is rejected before parsing (cheap substring check on the raw
    text), which blocks external-entity and billion-laughs attacks without
    adding a ``defusedxml`` dependency.
  * **Bounded everywhere.** Script count, script size, JSON depth (8), JSON node
    budget (5000), fan-out, regex matches, XML elements and result count are all
    capped; no loop iterates an untrusted structure without a ceiling.
  * **Never raises.** Every parse path is wrapped: malformed HTML / JSON / XML
    returns ``[]``.

Nothing here invents a document: a ``DiscoveredDocument`` is only ever a URL that
was literally present in the issuer's own served payload, on the issuer's own
allowlisted host.
"""

from __future__ import annotations

import contextvars
import json
import re
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from app.core.config import Settings
from app.services.sources.redaction import canonicalize_source_url
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    is_safe_public_host,
)
from app.services.sources.verified_issuer_sources import (
    host_of,
    registrable_host_allowed,
)

# --------------------------------------------------------------------------- #
# Vocabulary (neutral/factual — never a rating vocabulary)
# --------------------------------------------------------------------------- #

DOC_KIND_ANNUAL_REPORT = "annual_report"
DOC_KIND_INTERIM_REPORT = "interim_report"
DOC_KIND_RESULTS_RELEASE = "results_release"
DOC_KIND_PRESENTATION = "presentation"
DOC_KIND_OTHER = "other"

STRATEGY_ANCHORS = "anchors"
STRATEGY_JSON_LD = "json_ld"
STRATEGY_NEXT_DATA = "next_data"
STRATEGY_EMBEDDED_JSON = "embedded_json"
STRATEGY_FEED = "feed"
STRATEGY_JSON_ENDPOINT = "json_endpoint"

# The in-page strategies ``discover_documents`` runs, in order. ``feed`` and
# ``json_endpoint`` are deliberately NOT here: they need another (caller-driven,
# allowlisted) fetch, so the caller drives them explicitly.
DEFAULT_STRATEGIES: tuple[str, ...] = (
    STRATEGY_ANCHORS,
    STRATEGY_JSON_LD,
    STRATEGY_NEXT_DATA,
    STRATEGY_EMBEDDED_JSON,
)

# Ranking: an annual report beats a results release beats an interim report
# beats a presentation. Purely a *retrieval* preference — not a rating.
_KIND_RANK: dict[str, int] = {
    DOC_KIND_ANNUAL_REPORT: 0,
    DOC_KIND_RESULTS_RELEASE: 1,
    DOC_KIND_INTERIM_REPORT: 2,
    DOC_KIND_PRESENTATION: 3,
    DOC_KIND_OTHER: 4,
}
# Kinds that satisfy the "we have what we came for" early stop.
_PRIORITY_KINDS = (DOC_KIND_ANNUAL_REPORT, DOC_KIND_RESULTS_RELEASE)

# Classification keyword tables, checked in this precedence order so that e.g.
# "annual results presentation" classifies as an annual report, not a deck.
_ANNUAL_KEYWORDS: tuple[str, ...] = (
    "annual report",
    "universal registration document",
    "registration document",
    "integrated report",
    "annual financial report",
    "annual results",
    "full-year results",
    "full year results",
)
_INTERIM_KEYWORDS: tuple[str, ...] = (
    "half-year",
    "half year",
    "interim",
    "first-half",
    "first half",
    "quarterly",
)
# Short period tokens are matched on word boundaries only ("h1" must not hit
# "high1" or a hex hash fragment).
_INTERIM_SHORT_RE = re.compile(r"\b(?:h1|q1|q2|q3)\b")
_RESULTS_KEYWORDS: tuple[str, ...] = (
    "results release",
    "results announcement",
    "press release",
    "trading update",
    "earnings release",
)
_PRESENTATION_KEYWORDS: tuple[str, ...] = (
    "presentation",
    "slides",
    "deck",
    "webcast",
)

# File extensions that mark a directly downloadable document.
DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf",)

# --------------------------------------------------------------------------- #
# Hard bounds. Every one of these caps work over attacker-influenced input.
# --------------------------------------------------------------------------- #

DEFAULT_MAX_DOCUMENTS = 12
DEFAULT_MAX_JSON_ENDPOINTS = 5

_MAX_HTML_CHARS = 4_000_000
_MAX_FEED_CHARS = 4_000_000
_MAX_URL_CHARS = 2_000
_MAX_TITLE_CHARS = 200

_MAX_ANCHORS = 2_000
_MAX_SCRIPTS = 80
# Real SPA hydration payloads are large (several hundred KB is normal), so this
# cap sits just under the fetcher's own page-byte cap rather than below it.
_MAX_SCRIPT_CHARS = 1_000_000
_MAX_ATTR_VALUES = 800

_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 5_000
_MAX_JSON_FANOUT = 500
_MAX_JSON_LITERAL_CHARS = 200_000
_MAX_JSON_LITERALS_PER_SCRIPT = 5
_MAX_JSON_LITERAL_FAILURES = 3
_STATE_ASSIGNMENT_WINDOW = 200

_MAX_REGEX_MATCHES = 200
_MAX_XML_ELEMENTS = 5_000
_MAX_FEED_CONTAINERS = 200

# Script identifiers whose whole body is a JSON hydration payload.
_HYDRATION_SCRIPT_IDS: tuple[str, ...] = ("__next_data__", "__nuxt_data__", "__nuxt__")
# ``window.<name> = { ... };`` hydration assignments.
_STATE_ASSIGNMENT_NAMES: tuple[str, ...] = (
    "__initial_state__",
    "__preloaded_state__",
    "__initial_data__",
    "__nuxt__",
    "__apollo_state__",
)

# Attribute names that can carry a URL.
_URL_ATTRIBUTES: tuple[str, ...] = ("href", "src", "content", "action")

# XML tags (namespace-stripped) that can carry a document URL / title / date.
_FEED_CONTAINER_TAGS: tuple[str, ...] = ("item", "entry", "url")
_FEED_URL_TAGS: tuple[str, ...] = ("link", "loc", "guid", "enclosure")
_FEED_URL_ATTRS: tuple[str, ...] = ("href", "url")
_FEED_TITLE_TAGS: tuple[str, ...] = ("title", "name")
_FEED_DATE_TAGS: tuple[str, ...] = ("pubdate", "published", "updated", "lastmod", "date")

_ABS_DOC_RE = re.compile(r"(https://[^\s\"'<>()\\]{1,400}?\.pdf)", re.IGNORECASE)
_REL_DOC_RE = re.compile(r"[\"'](/[^\s\"'<>()\\]{1,400}?\.pdf)[\"']", re.IGNORECASE)
_QUOTED_URL_RE = re.compile(r"[\"'](https://[^\s\"'<>()\\]{1,400}|/[^\s\"'<>()\\]{1,400})[\"']")


@dataclass(frozen=True)
class DiscoveredDocument:
    """One bounded, allowlisted, secret-stripped document URL found on a page."""

    url: str
    title: str
    doc_kind: str
    strategy: str
    is_document: bool = False
    identity: str = ""
    published_hint: str | None = None


# --------------------------------------------------------------------------- #
# Classification + identity (pure, deterministic, never raising)
# --------------------------------------------------------------------------- #


def _haystacks(text: str, url: str) -> tuple[str, str]:
    """Return the raw lower-cased haystack and a separator-normalised variant.

    The normalised variant turns ``annual-report-2024.pdf`` into
    ``annual report 2024.pdf`` so URL slugs match the same keyword table as
    human link text. It only ever *widens* a match, so anchor behaviour from
    Slice 5A is preserved.
    """
    raw = f"{text or ''} {url or ''}".lower()
    normalised = raw.replace("%20", " ").replace("-", " ").replace("_", " ").replace("+", " ")
    return raw, normalised


def _contains_any(haystacks: tuple[str, str], keywords: tuple[str, ...]) -> bool:
    return any(kw in haystacks[0] or kw in haystacks[1] for kw in keywords)


def classify_document_kind(text: str, url: str) -> str:
    """Classify a document by link text + URL, deterministically.

    Precedence is annual > interim > results > presentation, so a mixed title
    such as "Annual results presentation 2024" classifies as an annual report.
    Never raises; unknown input classifies as ``other``.
    """
    try:
        hay = _haystacks(text, url)
    except (AttributeError, TypeError):
        return DOC_KIND_OTHER
    if _contains_any(hay, _ANNUAL_KEYWORDS):
        return DOC_KIND_ANNUAL_REPORT
    if _contains_any(hay, _INTERIM_KEYWORDS) or _INTERIM_SHORT_RE.search(hay[1]):
        return DOC_KIND_INTERIM_REPORT
    if _contains_any(hay, _RESULTS_KEYWORDS):
        return DOC_KIND_RESULTS_RELEASE
    if _contains_any(hay, _PRESENTATION_KEYWORDS):
        return DOC_KIND_PRESENTATION
    return DOC_KIND_OTHER


def document_identity(url: str) -> str:
    """Return the canonical dedup key for ``url``.

    Built from ``canonicalize_source_url`` (credential-free, no userinfo, no
    fragment), lower-cased, with the trailing slash normalised and — critically —
    the ENTIRE query string dropped, so signed / tokenised / cache-busted
    variants of the same document (``ar2024.pdf?token=a`` vs
    ``ar2024.pdf?token=b``) collapse to one identity. Never raises.
    """
    if not url:
        return ""
    try:
        canonical = canonicalize_source_url(url) or url
        parts = urlsplit(canonical)
        host = (parts.hostname or "").lower()
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        path = parts.path or "/"
        if len(path) > 1:
            path = path.rstrip("/") or "/"
        scheme = (parts.scheme or "https").lower()
        return f"{scheme}://{host}{path}".lower()
    except (ValueError, TypeError):
        return url.strip().lower()


def _title_from_url(url: str) -> str:
    """A human-ish fallback title derived from the URL's file name."""
    try:
        path = urlsplit(url).path
    except (ValueError, TypeError):
        return ""
    name = path.rsplit("/", 1)[-1]
    for ext in DOCUMENT_EXTENSIONS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return name.replace("-", " ").replace("_", " ").replace("%20", " ").strip()[:_MAX_TITLE_CHARS]


# --------------------------------------------------------------------------- #
# URL guards (pure — the SAME guards the fetchers apply, re-applied here)
# --------------------------------------------------------------------------- #


# The current issuer's curated document hosts, scoped to one discovery run.
#
# A ContextVar rather than a threaded parameter: the discovery strategies are a
# deep call tree and this is request-scoped, async-safe state. It defaults to
# EMPTY, so every issuer without curated document hosts behaves exactly as
# before, and it is always reset in a ``finally``.
_DOCUMENT_DOMAINS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "document_domains", default=()
)


def _is_document_url(url: str, document_domains: tuple[str, ...] | None = None) -> bool:
    """True when a URL is (or may be) a downloadable document.

    Two acceptance paths:

    1. A known document extension — the fast, unambiguous case.
    2. An EXTENSION-LESS URL on one of this issuer's curated
       ``document_domains``. Some issuers publish annual reports to a content
       CDN with no file suffix (Pandora's "Annual Report 2025" is served as
       ``application/pdf`` from a path with no extension and spaces in it), so
       suffix matching alone silently finds nothing.

    Path 2 only marks the link a CANDIDATE. The real type decision is made by
    the fetcher from the response ``Content-Type`` (see
    ``document_fetcher.classify_content_type``), so a curated host cannot make
    an HTML page masquerade as a PDF. And because ``document_domains`` is
    curated per issuer, no link discovered on a page can widen this set.
    """
    bare = url.lower().split("?", 1)[0].split("#", 1)[0]
    if bare.endswith(DOCUMENT_EXTENSIONS):
        return True
    if document_domains is None:
        document_domains = _DOCUMENT_DOMAINS.get()
    if not document_domains:
        return False
    if _has_known_non_document_extension(bare):
        return False
    return registrable_host_allowed(host_of(url), document_domains)


# Extensions that are definitely NOT downloadable documents. Keeps an
# extension-less-document rule from sweeping up a CDN's assets and scripts.
_NON_DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".mp3", ".json",
    ".xml", ".txt", ".zip", ".gz",
)


def _has_known_non_document_extension(bare_url: str) -> bool:
    name = bare_url.rsplit("/", 1)[-1]
    return name.endswith(_NON_DOCUMENT_EXTENSIONS)


_URL_PREFIXES = ("http://", "https://", "//", "/", "./", "../")


def _encode_url_spaces(value: str) -> str:
    """Percent-encode literal spaces in something already shaped like a URL.

    Official document URLs legitimately contain spaces — Pandora publishes
    ``.../v1/static/Annual Report 2025``. ``_looks_like_url_string`` rejects any
    whitespace, which is the right guard against free text in a JSON blob being
    urljoin-ed, but it also discarded those valid links.

    Encoding is applied ONLY to values that already start with a URL prefix, so
    the free-text guard is untouched: prose without a scheme or leading slash is
    still rejected by the prefix test below. Only the path is affected; query
    semantics are left alone.
    """
    if not value:
        return value
    text = value.strip()
    if not text.lower().startswith(_URL_PREFIXES) or " " not in text:
        return value
    head, sep, query = text.partition("?")
    return head.replace(" ", "%20") + sep + query


def _looks_like_url_string(value: str) -> bool:
    """Cheap shape test so free text in a JSON blob is never urljoin-ed."""
    if not value:
        return False
    text = value.strip()
    if not text or len(text) > _MAX_URL_CHARS:
        return False
    if any(ch.isspace() for ch in text):
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "//", "/", "./", "../")):
        return True
    return "/" in text and _is_document_url(lowered)


def _normalize_candidate_url(
    raw: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
) -> str | None:
    """Absolute-resolve, canonicalize and safety-check one candidate URL.

    Returns None unless the result is HTTPS, on a safe public host, and inside
    ``allowed_domains``. Never raises.

    Canonicalization uses ``canonicalize_source_url`` rather than
    ``strip_url_secrets``: the latter only drops credential-bearing QUERY
    parameters and would leave ``https://user:pass@host/…`` userinfo intact, which
    would then travel onto an ``EvidenceItem.url`` and into storage.
    """
    raw = _encode_url_spaces(raw)
    if not _looks_like_url_string(raw):
        return None
    text = raw.strip()
    if text.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    try:
        absolute = canonicalize_source_url(urljoin(base_url, text)) or ""
    except (ValueError, TypeError):
        return None
    if not absolute.startswith("https://") or len(absolute) > _MAX_URL_CHARS:
        return None
    host = host_of(absolute)
    if not is_safe_public_host(host):
        return None
    if not registrable_host_allowed(host, allowed_domains):
        return None
    return absolute


def _is_candidate_document(url: str, title: str, keywords: tuple[str, ...]) -> bool:
    """True when a URL is a downloadable document or matches document keywords."""
    return _is_document_url(url) or _contains_any(_haystacks(title, url), keywords)


def _make_document(
    url: str,
    title: str,
    strategy: str,
    *,
    published_hint: str | None = None,
) -> DiscoveredDocument:
    clean_title = (title or _title_from_url(url)).strip()[:_MAX_TITLE_CHARS]
    return DiscoveredDocument(
        url=url,
        title=clean_title,
        doc_kind=classify_document_kind(clean_title, url),
        strategy=strategy,
        is_document=_is_document_url(url),
        identity=document_identity(url),
        published_hint=(published_hint or None),
    )


def _add_document(
    out: list[DiscoveredDocument],
    seen: set[str],
    doc: DiscoveredDocument,
    max_documents: int,
) -> bool:
    """Append ``doc`` when new. Returns False when the cap has been reached."""
    if len(out) >= max_documents:
        return False
    if doc.identity in seen:
        return True
    seen.add(doc.identity)
    out.append(doc)
    return len(out) < max_documents


# --------------------------------------------------------------------------- #
# HTML parsing (stdlib html.parser only — no lxml, no bs4, no browser)
# --------------------------------------------------------------------------- #


class _DocumentParser(HTMLParser):
    """Collects anchors, ``<script>`` blocks and URL-bearing attribute values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (href, accumulated_text)
        self.anchors: list[tuple[str, str]] = []
        # (attrs, body)
        self.scripts: list[tuple[dict[str, str], str]] = []
        self.attr_values: list[str] = []
        self._cur_href: str | None = None
        self._cur_text: list[str] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_parts: list[str] = []
        self._script_len = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            self._script_attrs = a
            self._script_parts = []
            self._script_len = 0
        elif tag == "a" and a.get("href"):
            self._cur_href = a["href"].strip()
            self._cur_text = []
        for key, value in a.items():
            if len(self.attr_values) >= _MAX_ATTR_VALUES:
                break
            if not value:
                continue
            if key in _URL_ATTRIBUTES or key.startswith("data-"):
                self.attr_values.append(value.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._flush_script()
        elif tag == "a" and self._cur_href is not None:
            if len(self.anchors) < _MAX_ANCHORS:
                self.anchors.append((self._cur_href, " ".join(self._cur_text).strip()))
            self._cur_href = None
            self._cur_text = []

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            if self._script_len < _MAX_SCRIPT_CHARS:
                self._script_parts.append(data)
                self._script_len += len(data)
            return
        if self._cur_href is not None:
            self._cur_text.append(data)

    def _flush_script(self) -> None:
        if self._script_attrs is None:
            return
        if len(self.scripts) < _MAX_SCRIPTS:
            body = "".join(self._script_parts)[:_MAX_SCRIPT_CHARS]
            self.scripts.append((self._script_attrs, body))
        self._script_attrs = None
        self._script_parts = []
        self._script_len = 0

    def finish(self) -> None:
        """Flush an unterminated ``<script>`` so a truncated page still parses."""
        self._flush_script()


def _parse_document(html: str) -> _DocumentParser:
    parser = _DocumentParser()
    try:
        parser.feed((html or "")[:_MAX_HTML_CHARS])
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page must never raise
        pass
    parser.finish()
    return parser


def _script_type(attrs: dict[str, str]) -> str:
    return (attrs.get("type") or "").strip().lower()


def _script_id(attrs: dict[str, str]) -> str:
    return (attrs.get("id") or "").strip().lower()


# --------------------------------------------------------------------------- #
# JSON helpers (bounded literal extraction + bounded graph walk)
# --------------------------------------------------------------------------- #


def _strip_js_wrappers(text: str) -> str:
    """Strip whitespace / HTML-comment / CDATA / leading-JS-comment wrappers."""
    s = (text or "").strip()[:_MAX_SCRIPT_CHARS]
    if not s:
        return ""
    for marker in ("<!--", "//<![CDATA[", "/*<![CDATA[*/", "<![CDATA["):
        if s.startswith(marker):
            s = s[len(marker) :].strip()
    for marker in ("-->", "//]]>", "/*]]>*/", "]]>"):
        if s.endswith(marker):
            s = s[: -len(marker)].strip()
    if s.startswith("/*"):
        end = s.find("*/")
        if end >= 0:
            s = s[end + 2 :].strip()
    while s.startswith("//"):
        newline = s.find("\n")
        if newline < 0:
            return ""
        s = s[newline + 1 :].strip()
    return s


def _first_json_start(text: str, from_index: int = 0, window: int | None = None) -> int | None:
    """Index of the first ``{`` / ``[`` at or after ``from_index`` (optional window)."""
    limit = len(text) if window is None else min(len(text), from_index + window)
    for i in range(max(0, from_index), limit):
        if text[i] in "{[":
            return i
    return None


def _balanced_json_slice(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` / ``[...]`` literal beginning at ``start``.

    String-aware (quotes and escapes are respected) and hard-capped at
    ``_MAX_JSON_LITERAL_CHARS`` so an unterminated literal cannot scan forever.
    """
    if start < 0 or start >= len(text):
        return None
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth = 0
    in_string = False
    escaped = False
    limit = min(len(text), start + _MAX_JSON_LITERAL_CHARS)
    for i in range(start, limit):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _load_json(text: str) -> Any | None:
    """Parse ``text`` as JSON, tolerating wrappers / trailing junk. Never raises."""
    cleaned = _strip_js_wrappers(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001 - malformed JSON is skipped, never raised
        pass
    start = _first_json_start(cleaned)
    if start is None:
        return None
    literal = _balanced_json_slice(cleaned, start)
    if literal is None:
        return None
    try:
        return json.loads(literal)
    except Exception:  # noqa: BLE001 - malformed JSON is skipped, never raised
        return None


def _title_hint_from_dict(node: dict[str, Any]) -> str:
    for key in ("name", "headline", "title", "label", "description"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MAX_TITLE_CHARS]
    return ""


def _candidate_urls_from_json(
    node: Any,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_documents: int,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
) -> list[tuple[str, str]]:
    """Bounded walk of a decoded JSON graph returning ``(url, title_hint)`` pairs.

    Hard bounds: depth <= 8, <= 5000 visited nodes, <= 500 children per node.
    A string is a candidate only when it *looks* like a URL, resolves to an
    HTTPS URL on a safe, allowlisted host, and is either a document (``.pdf``)
    or matches the document keywords. Never raises.
    """
    out: list[tuple[str, str]] = []
    if max_documents <= 0:
        return out
    seen: set[str] = set()
    queue: deque[tuple[Any, int, str]] = deque([(node, 0, "")])
    visited = 0
    while queue:
        current, depth, hint = queue.popleft()
        visited += 1
        if visited > _MAX_JSON_NODES:
            break
        if depth > _MAX_JSON_DEPTH:
            continue
        if isinstance(current, str):
            absolute = _normalize_candidate_url(
                current, base_url=base_url, allowed_domains=allowed_domains
            )
            if not absolute or absolute in seen:
                continue
            if not _is_candidate_document(absolute, hint, keywords):
                continue
            seen.add(absolute)
            out.append((absolute, hint))
            if len(out) >= max_documents:
                break
        elif isinstance(current, dict):
            local_hint = _title_hint_from_dict(current) or hint
            for value in list(current.values())[:_MAX_JSON_FANOUT]:
                queue.append((value, depth + 1, local_hint))
        elif isinstance(current, list):
            for value in current[:_MAX_JSON_FANOUT]:
                queue.append((value, depth + 1, hint))
    return out


def _regex_document_urls(
    body: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
) -> list[str]:
    """Bounded regex sweep for absolute / root-relative document URLs.

    The fallback for a script whose payload is not valid JSON — minified JS
    object literals, or a hydration blob so large it was truncated by the script
    cap. Every hit still goes through the full URL guard.
    """
    urls: list[str] = []
    seen: set[str] = set()
    text = body[:_MAX_SCRIPT_CHARS]
    matches = 0
    for regex in (_ABS_DOC_RE, _REL_DOC_RE):
        for match in regex.finditer(text):
            matches += 1
            if matches > _MAX_REGEX_MATCHES:
                return urls
            absolute = _normalize_candidate_url(
                match.group(1), base_url=base_url, allowed_domains=allowed_domains
            )
            if not absolute or absolute in seen:
                continue
            seen.add(absolute)
            urls.append(absolute)
    return urls


def _documents_from_regex(
    body: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    strategy: str,
    out: list[DiscoveredDocument],
    seen: set[str],
    max_documents: int,
) -> bool:
    """Sweep one script body into ``out``. Returns False when the cap is hit."""
    for url in _regex_document_urls(body, base_url=base_url, allowed_domains=allowed_domains):
        if not _add_document(out, seen, _make_document(url, "", strategy), max_documents):
            return False
    return True


def _documents_from_json(
    data: Any,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...],
    strategy: str,
    out: list[DiscoveredDocument],
    seen: set[str],
    max_documents: int,
) -> bool:
    """Walk one decoded payload into ``out``. Returns False when the cap is hit."""
    pairs = _candidate_urls_from_json(
        data,
        base_url=base_url,
        allowed_domains=allowed_domains,
        max_documents=max_documents,
        keywords=keywords,
    )
    for url, hint in pairs:
        if not _add_document(out, seen, _make_document(url, hint, strategy), max_documents):
            return False
    return True


# --------------------------------------------------------------------------- #
# Strategy 1 — anchors (Slice 5A behaviour, preserved)
# --------------------------------------------------------------------------- #


def discover_from_anchors(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
) -> list[DiscoveredDocument]:
    """Extract keyword-matching ``<a href>`` document links (the Slice 5A path)."""
    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    try:
        parser = _parse_document(html)
        for href, text in parser.anchors:
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            if not _contains_any(_haystacks(text, href), keywords):
                continue
            absolute = _normalize_candidate_url(
                href, base_url=base_url, allowed_domains=allowed_domains
            )
            if not absolute:
                continue
            doc = _make_document(absolute, text, STRATEGY_ANCHORS)
            if not _add_document(out, seen, doc, max_documents):
                break
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Strategy 2 — JSON-LD (<script type="application/ld+json">)
# --------------------------------------------------------------------------- #


def discover_from_json_ld(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
) -> list[DiscoveredDocument]:
    """Extract document URLs from schema.org JSON-LD blocks."""
    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    try:
        for attrs, body in _parse_document(html).scripts:
            if "ld+json" not in _script_type(attrs):
                continue
            data = _load_json(body)
            if data is None:
                continue
            if not _documents_from_json(
                data,
                base_url=base_url,
                allowed_domains=allowed_domains,
                keywords=keywords,
                strategy=STRATEGY_JSON_LD,
                out=out,
                seen=seen,
                max_documents=max_documents,
            ):
                break
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Strategy 3 — framework hydration state (__NEXT_DATA__ / __INITIAL_STATE__)
# --------------------------------------------------------------------------- #


def _state_payloads(body: str) -> list[Any]:
    """Decode ``window.__INITIAL_STATE__ = {...};`` style hydration assignments."""
    payloads: list[Any] = []
    lowered = body.lower()
    for name in _STATE_ASSIGNMENT_NAMES:
        pos = lowered.find(name)
        if pos < 0:
            continue
        eq = body.find("=", pos + len(name))
        if eq < 0:
            continue
        start = _first_json_start(body, eq + 1, window=_STATE_ASSIGNMENT_WINDOW)
        if start is None:
            continue
        literal = _balanced_json_slice(body, start)
        if literal is None:
            continue
        data = _load_json(literal)
        if data is not None:
            payloads.append(data)
        if len(payloads) >= _MAX_JSON_LITERALS_PER_SCRIPT:
            break
    return payloads


def discover_from_next_data(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
) -> list[DiscoveredDocument]:
    """Extract document URLs from SPA hydration state embedded in the page.

    This is the core Slice 5B fix: a Next.js / Nuxt / Redux page can render its
    entire document list client-side, leaving no anchor in the served HTML while
    the official PDF URLs sit in the hydration payload.
    """
    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    try:
        for attrs, body in _parse_document(html).scripts:
            payloads: list[Any] = []
            is_hydration_script = _script_id(attrs) in _HYDRATION_SCRIPT_IDS
            if is_hydration_script:
                data = _load_json(body)
                if data is not None:
                    payloads.append(data)
            if not payloads:
                payloads.extend(_state_payloads(body))
            for payload in payloads:
                if not _documents_from_json(
                    payload,
                    base_url=base_url,
                    allowed_domains=allowed_domains,
                    keywords=keywords,
                    strategy=STRATEGY_NEXT_DATA,
                    out=out,
                    seen=seen,
                    max_documents=max_documents,
                ):
                    return out
            # A hydration payload that did not decode (minified JS, or a blob so
            # large it hit the script cap) still gets the bounded regex sweep —
            # otherwise the very pages this slice exists for yield nothing.
            if is_hydration_script and not payloads:
                if not _documents_from_regex(
                    body,
                    base_url=base_url,
                    allowed_domains=allowed_domains,
                    strategy=STRATEGY_NEXT_DATA,
                    out=out,
                    seen=seen,
                    max_documents=max_documents,
                ):
                    return out
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Strategy 4 — any other embedded script JSON (with a regex fallback)
# --------------------------------------------------------------------------- #


def _json_literals(body: str) -> list[Any]:
    """Up to ``_MAX_JSON_LITERALS_PER_SCRIPT`` decoded literals inside a script."""
    payloads: list[Any] = []
    index = 0
    failures = 0
    while len(payloads) < _MAX_JSON_LITERALS_PER_SCRIPT and index < len(body):
        start = _first_json_start(body, index)
        if start is None:
            break
        literal = _balanced_json_slice(body, start)
        if literal is None:
            failures += 1
            if failures >= _MAX_JSON_LITERAL_FAILURES:
                break
            index = start + 1
            continue
        data = _load_json(literal)
        if data is not None:
            payloads.append(data)
        else:
            failures += 1
            if failures >= _MAX_JSON_LITERAL_FAILURES:
                break
        index = start + max(1, len(literal))
    return payloads


def discover_from_embedded_json(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
) -> list[DiscoveredDocument]:
    """Extract document URLs from ordinary inline ``<script>`` payloads.

    Cheap prefilter: only scripts whose body mentions ``.pdf`` are inspected.
    JSON literals are parsed and walked; when nothing parses, a bounded regex
    sweep picks up the URLs directly.
    """
    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    try:
        for attrs, body in _parse_document(html).scripts:
            if "ld+json" in _script_type(attrs):
                continue
            if _script_id(attrs) in _HYDRATION_SCRIPT_IDS:
                continue
            if ".pdf" not in body.lower():
                continue
            before = len(out)
            capped = False
            for payload in _json_literals(body):
                if not _documents_from_json(
                    payload,
                    base_url=base_url,
                    allowed_domains=allowed_domains,
                    keywords=keywords,
                    strategy=STRATEGY_EMBEDDED_JSON,
                    out=out,
                    seen=seen,
                    max_documents=max_documents,
                ):
                    capped = True
                    break
            if capped:
                break
            if len(out) > before:
                continue
            if not _documents_from_regex(
                body,
                base_url=base_url,
                allowed_domains=allowed_domains,
                strategy=STRATEGY_EMBEDDED_JSON,
                out=out,
                seen=seen,
                max_documents=max_documents,
            ):
                break
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Strategy 5 — RSS / Atom / sitemap XML (text fetched by the CALLER)
# --------------------------------------------------------------------------- #


def _local_tag(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].strip().lower()


def _bounded_elements(root: ElementTree.Element) -> list[ElementTree.Element]:
    elements: list[ElementTree.Element] = []
    for element in root.iter():
        elements.append(element)
        if len(elements) >= _MAX_XML_ELEMENTS:
            break
    return elements


def _first_child_text(elements: list[ElementTree.Element], tags: tuple[str, ...]) -> str:
    for element in elements:
        if _local_tag(element.tag) in tags:
            text = (element.text or "").strip()
            if text:
                return text[:_MAX_TITLE_CHARS]
    return ""


def discover_from_feed(
    xml_text: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
) -> list[DiscoveredDocument]:
    """Extract document URLs from an RSS / Atom / sitemap document.

    XXE / billion-laughs guard: any document containing ``<!DOCTYPE`` or
    ``<!ENTITY`` is rejected outright BEFORE parsing, so no entity can ever be
    defined or expanded (this is why no ``defusedxml`` dependency is needed).
    Never raises; malformed XML returns ``[]``.
    """
    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    raw = xml_text or ""
    if not raw.strip() or len(raw) > _MAX_FEED_CHARS:
        return out
    lowered = raw.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return out
    try:
        root = ElementTree.fromstring(raw, parser=ElementTree.XMLParser())
    except Exception:  # noqa: BLE001 - malformed XML must never raise
        return out
    try:
        elements = _bounded_elements(root)
        containers = [el for el in elements if _local_tag(el.tag) in _FEED_CONTAINER_TAGS][
            :_MAX_FEED_CONTAINERS
        ]
        if not containers:
            containers = [root]
        for container in containers:
            children = _bounded_elements(container)
            title = _first_child_text(children, _FEED_TITLE_TAGS)
            published = _first_child_text(children, _FEED_DATE_TAGS)
            for element in children:
                if _local_tag(element.tag) not in _FEED_URL_TAGS:
                    continue
                raw_urls = [(element.text or "").strip()]
                for attr in _FEED_URL_ATTRS:
                    value = element.attrib.get(attr)
                    if value:
                        raw_urls.append(value.strip())
                for raw_url in raw_urls:
                    absolute = _normalize_candidate_url(
                        raw_url, base_url=base_url, allowed_domains=allowed_domains
                    )
                    if not absolute:
                        continue
                    if not _is_candidate_document(absolute, title, keywords):
                        continue
                    doc = _make_document(absolute, title, STRATEGY_FEED, published_hint=published)
                    if not _add_document(out, seen, doc, max_documents):
                        return out
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Same-origin JSON data endpoints (REPORTED, never fetched here)
# --------------------------------------------------------------------------- #


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    scheme = (parts.scheme or "").lower()
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{scheme}://{host}{port}"


def _looks_like_json_endpoint(url: str) -> bool:
    try:
        path = (urlsplit(url).path or "").lower()
    except (ValueError, TypeError):
        return False
    return path.endswith(".json") or "/api/" in path


def find_json_endpoints(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    max_endpoints: int = DEFAULT_MAX_JSON_ENDPOINTS,
) -> list[str]:
    """Return bounded SAME-ORIGIN JSON/data endpoint URLs referenced by the page.

    These are *reported* for the caller to decide about — this module never
    fetches anything. Every returned URL is HTTPS, on a safe public host, inside
    ``allowed_domains``, and on the same origin as ``base_url`` (so a page can
    never point discovery at a third party). Never raises.
    """
    out: list[str] = []
    if max_endpoints <= 0:
        return out
    origin = _origin_of(base_url)
    if not origin:
        return out
    seen: set[str] = set()
    try:
        parser = _parse_document(html)
        candidates: list[str] = list(parser.attr_values)
        for _attrs, body in parser.scripts:
            matches = 0
            for match in _QUOTED_URL_RE.finditer(body[:_MAX_SCRIPT_CHARS]):
                matches += 1
                if matches > _MAX_REGEX_MATCHES:
                    break
                candidates.append(match.group(1))
        for raw in candidates[: _MAX_ATTR_VALUES + _MAX_REGEX_MATCHES]:
            absolute = _normalize_candidate_url(
                raw, base_url=base_url, allowed_domains=allowed_domains
            )
            if not absolute or absolute in seen:
                continue
            if _origin_of(absolute) != origin:
                continue
            if not _looks_like_json_endpoint(absolute):
                continue
            seen.add(absolute)
            out.append(absolute)
            if len(out) >= max_endpoints:
                break
    except Exception:  # noqa: BLE001 - discovery must never crash a run
        return out
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def rank_documents(documents: list[DiscoveredDocument]) -> list[DiscoveredDocument]:
    """Stable-rank by kind (annual first), then downloadable documents first."""
    return sorted(
        documents,
        key=lambda d: (_KIND_RANK.get(d.doc_kind, len(_KIND_RANK)), 0 if d.is_document else 1),
    )


def discover_documents(
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    cfg: Settings | None = None,
    keywords: tuple[str, ...] = ANNUAL_REPORT_KEYWORDS,
    max_documents: int | None = None,
    strategies: tuple[str, ...] | None = None,
    document_domains: tuple[str, ...] = (),
) -> list[DiscoveredDocument]:
    """Run every in-page discovery strategy over ONE already-fetched page.

    Order is anchors -> json_ld -> next_data -> embedded_json; results are
    de-duplicated by ``identity`` after each strategy, so a document present in
    both an anchor and the hydration payload appears once (attributed to the
    strategy that found it first). Discovery stops early once ``max_documents``
    annual-report / results-release documents exist, then everything is ranked
    and truncated. Never raises.

    Feeds and JSON endpoints are NOT run here: they require another fetch, which
    only the caller (with the allowlisted, SSRF-guarded fetcher) may perform.
    """
    cap = max_documents
    if cap is None:
        cap = getattr(cfg, "primary_document_max_discovery_candidates", None)
    if not isinstance(cap, int) or cap <= 0:
        cap = DEFAULT_MAX_DOCUMENTS
    wanted = tuple(strategies) if strategies is not None else DEFAULT_STRATEGIES

    out: list[DiscoveredDocument] = []
    seen: set[str] = set()
    # Scope the curated document hosts to THIS run, and always reset.
    token = _DOCUMENT_DOMAINS.set(tuple(document_domains or ()))
    try:
        for strategy in DEFAULT_STRATEGIES:
            if strategy not in wanted:
                continue
            try:
                found = _run_strategy(
                    strategy,
                    html,
                    base_url=base_url,
                    allowed_domains=allowed_domains,
                    keywords=keywords,
                    max_documents=cap,
                )
            except Exception:  # noqa: BLE001 - one bad strategy must not kill the rest
                found = []
            for doc in found:
                if doc.identity in seen:
                    continue
                seen.add(doc.identity)
                out.append(doc)
            priority = sum(1 for d in out if d.doc_kind in _PRIORITY_KINDS)
            if priority >= cap:
                break
    finally:
        _DOCUMENT_DOMAINS.reset(token)
    return rank_documents(out)[:cap]


def _run_strategy(
    strategy: str,
    html: str,
    *,
    base_url: str,
    allowed_domains: tuple[str, ...],
    keywords: tuple[str, ...],
    max_documents: int,
) -> list[DiscoveredDocument]:
    if strategy == STRATEGY_ANCHORS:
        return discover_from_anchors(
            html,
            base_url=base_url,
            allowed_domains=allowed_domains,
            keywords=keywords,
            max_documents=max_documents,
        )
    if strategy == STRATEGY_JSON_LD:
        return discover_from_json_ld(
            html,
            base_url=base_url,
            allowed_domains=allowed_domains,
            max_documents=max_documents,
            keywords=keywords,
        )
    if strategy == STRATEGY_NEXT_DATA:
        return discover_from_next_data(
            html,
            base_url=base_url,
            allowed_domains=allowed_domains,
            max_documents=max_documents,
            keywords=keywords,
        )
    if strategy == STRATEGY_EMBEDDED_JSON:
        return discover_from_embedded_json(
            html,
            base_url=base_url,
            allowed_domains=allowed_domains,
            max_documents=max_documents,
            keywords=keywords,
        )
    return []


__all__ = [
    "DEFAULT_MAX_DOCUMENTS",
    "DEFAULT_MAX_JSON_ENDPOINTS",
    "DEFAULT_STRATEGIES",
    "DOCUMENT_EXTENSIONS",
    "DOC_KIND_ANNUAL_REPORT",
    "DOC_KIND_INTERIM_REPORT",
    "DOC_KIND_OTHER",
    "DOC_KIND_PRESENTATION",
    "DOC_KIND_RESULTS_RELEASE",
    "STRATEGY_ANCHORS",
    "STRATEGY_EMBEDDED_JSON",
    "STRATEGY_FEED",
    "STRATEGY_JSON_ENDPOINT",
    "STRATEGY_JSON_LD",
    "STRATEGY_NEXT_DATA",
    "DiscoveredDocument",
    "classify_document_kind",
    "discover_documents",
    "discover_from_anchors",
    "discover_from_embedded_json",
    "discover_from_feed",
    "discover_from_json_ld",
    "discover_from_next_data",
    "document_identity",
    "find_json_endpoints",
    "rank_documents",
]
