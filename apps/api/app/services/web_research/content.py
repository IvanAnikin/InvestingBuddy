"""Content sniffing, per-class byte caps and charset — open-web W2 (threat model §4).

Nothing here parses a document into evidence (that is W3). This module answers three
questions about bytes that were fetched:

1. **What are they?** :func:`sniff` reads magic bytes and routes by the SNIFFED type,
   never by the served ``Content-Type`` or the URL's extension (FILE-01, FILE-10). The
   served type is recorded beside it and a disagreement is flagged.
2. **How much may we hold?** :data:`CLASS_BYTE_CAPS` — HTML 3 MB, PDF 35 MB, other text
   2 MB (FILE-02). Office containers (``PK\\x03\\x04``, OLE2) and every binary we do not
   handle are refused as ``unsupported_type`` after reading only the sniff prefix.
3. **How is the text encoded?** :func:`detect_charset` — BOM, then the HTTP header, then
   ``<meta charset>`` / ``<?xml encoding>``, then ``charset_normalizer`` over a bounded
   sample. WHATWG's rule that ``iso-8859-1``/``us-ascii`` labels mean ``windows-1252`` is
   applied, because that is what the page's author saw in a browser.

Plus one heuristic the browser decision rule needs (spec §9.4): :func:`js_required` —
the body is over 20 KB, its visible text is under 500 characters, and SPA markers are
present.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

CLASS_HTML = "html"
CLASS_PDF = "pdf"
CLASS_TEXT = "text"
CLASS_OFFICE = "office"
CLASS_BINARY = "binary"

#: Decimal megabytes, as in the threat model table and ``source_document_*`` config.
CLASS_BYTE_CAPS: dict[str, int] = {
    CLASS_HTML: 3_000_000,
    CLASS_PDF: 35_000_000,
    CLASS_TEXT: 2_000_000,
}
#: The largest cap: what a fetch may read before the sniff narrows it.
MAX_CLASS_BYTES = max(CLASS_BYTE_CAPS.values())
SUPPORTED_CLASSES: frozenset[str] = frozenset(CLASS_BYTE_CAPS)

MIME_HTML = "text/html"
MIME_PDF = "application/pdf"
MIME_TEXT = "text/plain"
MIME_JSON = "application/json"
MIME_XML = "application/xml"
MIME_SVG = "image/svg+xml"
MIME_ZIP = "application/zip"
MIME_OLE = "application/x-ole-storage"
MIME_OCTET = "application/octet-stream"

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

#: WHATWG "HTML" sniffing patterns: a tag name followed by a tag-terminating byte.
_HTML_TAGS: tuple[bytes, ...] = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<script",
    b"<iframe",
    b"<h1",
    b"<div",
    b"<font",
    b"<table",
    b"<a",
    b"<style",
    b"<title",
    b"<b",
    b"<body",
    b"<br",
    b"<p",
    b"<!--",
)
_TAG_TERMINATORS = b" >\t\n\r\x0c"

_BINARY_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"PK\x03\x04", MIME_ZIP, CLASS_OFFICE),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", MIME_OLE, CLASS_OFFICE),
    (b"\x89PNG\r\n\x1a\n", "image/png", CLASS_BINARY),
    (b"\xff\xd8\xff", "image/jpeg", CLASS_BINARY),
    (b"GIF87a", "image/gif", CLASS_BINARY),
    (b"GIF89a", "image/gif", CLASS_BINARY),
    (b"\x1f\x8b", "application/gzip", CLASS_BINARY),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed", CLASS_BINARY),
    (b"Rar!\x1a\x07", "application/vnd.rar", CLASS_BINARY),
    (b"%!PS", "application/postscript", CLASS_BINARY),
    (b"\x00\x00\x01\x00", "image/x-icon", CLASS_BINARY),
)


@dataclass(frozen=True)
class Sniffed:
    mime: str
    content_class: str
    bom: str | None = None

    @property
    def supported(self) -> bool:
        return self.content_class in SUPPORTED_CLASSES


def _strip_bom(prefix: bytes) -> tuple[bytes, str | None]:
    for bom, name in _BOMS:
        if prefix.startswith(bom):
            return prefix[len(bom) :], name
    return prefix, None


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False
    control = sum(1 for b in data if b < 0x09 or 0x0E <= b < 0x20 or b == 0x7F)
    return control * 100 <= len(data)


def sniff(prefix: bytes) -> Sniffed:
    """Name the content from its first bytes. Never raises; never trusts a header."""
    raw = prefix or b""
    body, bom = _strip_bom(raw)
    if bom and bom.startswith(("utf-16", "utf-32")):
        # A UTF-16/32 BOM: re-read the prefix as text to sniff markup.
        try:
            text = body.decode(bom, "ignore").encode("utf-8", "ignore")
        except (LookupError, UnicodeError):
            text = b""
        inner = sniff(text)
        return Sniffed(inner.mime, inner.content_class, bom)
    for signature, mime, cls in _BINARY_SIGNATURES:
        if body.startswith(signature):
            return Sniffed(mime, cls, bom)
    stripped = body.lstrip(b" \t\r\n\x0c")
    if stripped.startswith(b"%PDF-"):
        return Sniffed(MIME_PDF, CLASS_PDF, bom)
    lowered = stripped[:512].lower()
    if lowered.startswith(b"<?xml"):
        if b"<svg" in lowered:
            return Sniffed(MIME_SVG, CLASS_BINARY, bom)
        if b"<html" in lowered or b"<!doctype html" in lowered:
            return Sniffed(MIME_HTML, CLASS_HTML, bom)
        return Sniffed(MIME_XML, CLASS_TEXT, bom)
    if lowered.startswith(b"<svg"):
        return Sniffed(MIME_SVG, CLASS_BINARY, bom)
    for tag in _HTML_TAGS:
        if lowered.startswith(tag):
            nxt = lowered[len(tag) : len(tag) + 1]
            if tag == b"<!--" or (nxt and nxt in _TAG_TERMINATORS):
                return Sniffed(MIME_HTML, CLASS_HTML, bom)
    if not _looks_like_text(stripped[:1024]):
        return Sniffed(MIME_OCTET, CLASS_BINARY, bom)
    if stripped[:1] in (b"{", b"["):
        return Sniffed(MIME_JSON, CLASS_TEXT, bom)
    return Sniffed(MIME_TEXT, CLASS_TEXT, bom)


def cap_for_prefix(prefix: bytes, ceiling: int) -> int:
    """The byte cap for the class ``prefix`` reveals, never above ``ceiling``.

    A refused class returns the prefix length: reading stops at the sniff prefix.
    """
    sniffed = sniff(prefix)
    if not sniffed.supported:
        return len(prefix)
    return max(1, min(int(ceiling), CLASS_BYTE_CAPS[sniffed.content_class]))


# --------------------------------------------------------------------------- #
# Served type
# --------------------------------------------------------------------------- #


def media_type(content_type: str | None) -> str | None:
    """``text/html; charset=x`` → ``text/html``; None for an absent header."""
    value = (content_type or "").split(";", 1)[0].strip().lower()
    return value[:120] or None


def header_charset(content_type: str | None) -> str | None:
    for param in (content_type or "").split(";")[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            cleaned = value.strip().strip("\"'").strip()
            return cleaned[:40] or None
    return None


def served_class(mime: str | None) -> str | None:
    """The class a served media type CLAIMS, or None when it claims nothing specific."""
    if not mime or mime in (MIME_OCTET, "binary/octet-stream", "application/unknown"):
        return None
    if mime in ("text/html", "application/xhtml+xml"):
        return CLASS_HTML
    if mime in ("application/pdf", "application/x-pdf"):
        return CLASS_PDF
    if (
        mime.startswith("text/")
        or mime in (MIME_JSON, MIME_XML)
        or mime.endswith(("+json", "+xml"))
    ) and mime != MIME_SVG:
        return CLASS_TEXT
    if (
        mime.startswith("application/vnd.openxmlformats")
        or mime.startswith("application/vnd.ms-")
        or mime in ("application/msword", MIME_ZIP)
    ):
        return CLASS_OFFICE
    return CLASS_BINARY


def mime_mismatch(served_mime: str | None, sniffed: Sniffed, url_path: str = "") -> bool:
    """True when the served type or a ``.pdf`` extension disagrees with the bytes."""
    claimed = served_class(served_mime)
    if claimed is not None and claimed != sniffed.content_class:
        return True
    return url_path.lower().endswith(".pdf") and sniffed.content_class != CLASS_PDF


# --------------------------------------------------------------------------- #
# Charset
# --------------------------------------------------------------------------- #

#: WHATWG Encoding Standard: these labels decode as windows-1252.
_WINDOWS_1252_LABELS = frozenset(
    {
        "ascii",
        "us-ascii",
        "iso-8859-1",
        "iso8859-1",
        "iso_8859-1",
        "iso88591",
        "latin1",
        "latin-1",
        "l1",
        "cp819",
        "ibm819",
        "windows-1252",
        "cp1252",
        "x-cp1252",
    }
)
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_.:-]{1,40})""", re.IGNORECASE
)
_XML_ENCODING_RE = re.compile(
    rb"""<\?xml[^>]*encoding\s*=\s*["']([a-zA-Z0-9_.:-]{1,40})["']""", re.IGNORECASE
)
_META_SCAN_BYTES = 4096
_DETECT_SAMPLE_BYTES = 200_000

CHARSET_BOM = "bom"
CHARSET_HEADER = "header"
CHARSET_META = "meta"
CHARSET_DETECTED = "detected"
CHARSET_DEFAULT = "default"


def normalise_charset(label: str | None) -> str | None:
    """A Python codec name for ``label``, or None when it is not a real encoding."""
    if not label:
        return None
    cleaned = label.strip().lower()
    if cleaned in _WINDOWS_1252_LABELS:
        return "cp1252"
    try:
        return codecs.lookup(cleaned).name
    except LookupError:
        return None


def detect_charset(body: bytes, *, content_type: str | None, content_class: str) -> tuple[str, str]:
    """``(codec, source)`` for decoding ``body``. Source is how it was decided."""
    _, bom = _strip_bom(body[:4])
    if bom:
        return bom, CHARSET_BOM
    from_header = normalise_charset(header_charset(content_type))
    if from_header:
        return from_header, CHARSET_HEADER
    head = body[:_META_SCAN_BYTES]
    for pattern in (_META_CHARSET_RE, _XML_ENCODING_RE):
        m = pattern.search(head)
        if m:
            label = m.group(1).decode("ascii", "ignore")
            codec = normalise_charset(label)
            if codec and codec.startswith(("utf-16", "utf-32")):
                # WHATWG: a UTF-16 label readable as ASCII cannot be true.
                codec = "utf-8"
            if codec:
                return codec, CHARSET_META
    if content_class in (CLASS_HTML, CLASS_TEXT) and body:
        try:
            if body[:_DETECT_SAMPLE_BYTES].isascii():
                return "utf-8", CHARSET_DETECTED
            body[:_DETECT_SAMPLE_BYTES].decode("utf-8")
            return "utf-8", CHARSET_DETECTED
        except UnicodeDecodeError:
            pass
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(body[:_DETECT_SAMPLE_BYTES]).best()
        except Exception:  # noqa: BLE001 - detection is best effort
            best = None
        if best is not None and best.encoding:
            codec = normalise_charset(best.encoding)
            if codec:
                return codec, CHARSET_DETECTED
    return "utf-8", CHARSET_DEFAULT


def decode_text(body: bytes, codec: str) -> str:
    """Decode with ``codec``; undecodable bytes become U+FFFD, never an exception."""
    try:
        text = body.decode(codec, "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    return text.lstrip("﻿")


# --------------------------------------------------------------------------- #
# Visible text and the js_required heuristic (spec §9.4)
# --------------------------------------------------------------------------- #

JS_REQUIRED_MIN_BODY_BYTES = 20_000
JS_REQUIRED_MAX_VISIBLE_CHARS = 500
_SPA_MARKERS: tuple[str, ...] = (
    'id="root"',
    "id='root'",
    'id="__next"',
    "__next_data__",
    'id="app"',
    "id='app'",
    "ng-version",
    "ng-app",
    "data-reactroot",
    "window.__initial_state__",
    "window.__nuxt__",
    'id="__nuxt"',
    "data-server-rendered",
    "you need to enable javascript",
    "please enable javascript",
    "requires javascript",
)


#: A start tag longer than this is not treated as a tag by the scanners below.
MAX_TAG_CHARS = 4096


def iter_start_tag_spans(
    html: str, name: str, *, max_chars: int = MAX_CLASS_BYTES
) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, tag)`` for each ``<name …>`` start tag. LINEAR.

    Every scan in W2 that looks for a tag (``<link rel=canonical>``, ``<meta>``,
    ``<input type=password>``, JSON-LD ``<script>``) goes through here rather than a
    ``<name[^>]*>`` regex: on hostile input with many openers and no ``>`` such a regex
    re-scans the rest of the page from every opener (quadratic). This uses ``str.find``
    only, and stops at the first opener that is never closed. ``end`` is exclusive.
    """
    text = html[:max_chars]
    lowered = text.lower()
    needle = "<" + name.lower()
    pos = 0
    while True:
        start = lowered.find(needle, pos)
        if start == -1:
            return
        after = start + len(needle)
        nxt = lowered[after : after + 1]
        if nxt and nxt not in " \t\n\r\x0c/>":
            pos = after  # ``<linker>`` is not ``<link>``
            continue
        close = lowered.find(">", after)
        if close == -1:
            return
        if close - start <= MAX_TAG_CHARS:
            yield start, close + 1, text[start : close + 1]
        pos = close + 1


def iter_start_tags(html: str, name: str, *, max_chars: int = MAX_CLASS_BYTES) -> Iterator[str]:
    """The tags of :func:`iter_start_tag_spans`."""
    for _start, _end, tag in iter_start_tag_spans(html, name, max_chars=max_chars):
        yield tag


#: A comment or an invisible block's opener. ``[^<>]*`` cannot run past the next ``<``,
#: so a search never re-scans the page (linear even on hostile markup).
_HIDDEN_START_RE = re.compile(r"<!--|<(script|style|noscript|template|head|svg)\b[^<>]*>")
#: A real tag (``<`` + optional ``/!?`` + a letter). A stray ``<`` in text is kept.
_ANY_TAG_RE = re.compile(r"<[/!?]?[a-zA-Z][^<>]*>")


def visible_text_chars(html: str) -> int:
    """Characters of visible text (scripts, styles, the head and comments excluded).

    Linear, not a DOM: this feeds two thresholds only (the ``js_required`` heuristic
    and "is this page small enough to be a wall"). A real parser took ~14 s on a 3 MB
    page, and a ``<tag[^>]*>`` regex is quadratic on hostile markup. Whitespace is
    collapsed; character references count as written.
    """
    text = html[:MAX_CLASS_BYTES]
    lowered = text.lower()
    n = len(text)
    keep: list[str] = []
    pos = 0
    while pos < n:
        m = _HIDDEN_START_RE.search(lowered, pos)
        if m is None:
            keep.append(text[pos:])
            break
        keep.append(text[pos : m.start()])
        if m.group(1) is None:  # a comment
            end = lowered.find("-->", m.end())
            pos = n if end == -1 else end + 3
            continue
        if m.group(0).endswith("/>"):
            pos = m.end()
            continue
        end = lowered.find("</" + m.group(1), m.end())
        if end == -1:
            break
        close = lowered.find(">", end)
        pos = n if close == -1 else close + 1
    visible = _ANY_TAG_RE.sub(" ", " ".join(keep))
    return len(" ".join(visible.split()))


def js_required(body_bytes: int, html: str, visible_chars: Callable[[], int] | None = None) -> bool:
    """Spec §9.4: body > 20 KB, visible text < 500 characters, and SPA markers.

    The cheap tests run first; visible text is counted only for a large page that
    carries an SPA marker (``visible_chars`` lets the caller share one lazy count).
    """
    if body_bytes <= JS_REQUIRED_MIN_BODY_BYTES:
        return False
    lowered = html[:MAX_CLASS_BYTES].lower()
    if not any(marker in lowered for marker in _SPA_MARKERS):
        return False
    chars = visible_chars() if visible_chars is not None else visible_text_chars(html)
    return chars < JS_REQUIRED_MAX_VISIBLE_CHARS


__all__ = [
    "CHARSET_BOM",
    "CHARSET_DEFAULT",
    "CHARSET_DETECTED",
    "CHARSET_HEADER",
    "CHARSET_META",
    "CLASS_BINARY",
    "CLASS_BYTE_CAPS",
    "CLASS_HTML",
    "CLASS_OFFICE",
    "CLASS_PDF",
    "CLASS_TEXT",
    "MAX_CLASS_BYTES",
    "Sniffed",
    "cap_for_prefix",
    "decode_text",
    "detect_charset",
    "header_charset",
    "iter_start_tag_spans",
    "iter_start_tags",
    "js_required",
    "media_type",
    "mime_mismatch",
    "normalise_charset",
    "served_class",
    "sniff",
    "visible_text_chars",
]
