"""HTML and PDF extraction for open-web documents — open-web W3 (spec §10).

WHAT GOES IN, WHAT COMES OUT
============================
Bytes InvestingBuddy fetched itself (``fetch.open_web_fetch``), never a URL: nothing in
this module can make a network request. Out comes a :class:`WebExtraction` whose
``extraction`` is the SAME ``PrimaryDocumentExtraction`` shape filings and NSM/ASX
disclosures produce, so web documents enter the corpus through the one bridge
(``ingest_extracted_document``) with blocks, tables and page lineage (spec §10.5).

HTML (spec §10.1)
=================
1. Input capped at 3 MB; parsed ONCE by lxml with ``huge_tree`` off and no network.
2. Hidden content is removed BEFORE main-content extraction — ``<script>``/``<style>``/
   ``<template>``/``<noscript>``, comments, ``hidden``, ``aria-hidden="true"``, inline
   ``display:none`` / ``visibility:hidden`` / zero size / off-screen / transparent
   text, screen-reader-only classes (threat model §3.3). The removed text is kept
   (bounded) only as an injection-taint SIGNAL; the raw bytes keep everything.
3. ``trafilatura.bare_extraction`` on that tree — tables on, links off, metadata on.
   Its downloader is never used. Under 300 characters of main text → the existing
   stdlib extractor (container mode) and ``extraction_confidence="low"``. A JS-only
   shell that yields nothing stays empty: no text is ever invented.
4. Metadata: title, ``published_at`` with its SOURCE (``json_ld | meta | url | text``),
   author, site name, OpenGraph/JSON-LD ``@type``, ``rel=canonical``, language (``<html
   lang>`` → ``Content-Language`` → script → the existing stopword heuristic), headings,
   licence signals, ``citation_*`` presence.
5. SVG is never parsed; XML with a DOCTYPE/ENTITY is refused (FILE-05).

PDF (spec §10.2)
================
Routed to ``extract_primary_document``. A document with more pages than the layout
budget runs in TWO passes: (1) pypdf text for up to 400 pages, each page scored against
the run's query terms, the intent vocabulary and the TOC; (2) pdfplumber layout + tables
on the top N pages (STANDARD 25, DEEP 60) plus the existing 12 bookmark-targeted
statement pages. Every text-pass page becomes blocks with its page number; only layout
pages yield tables. ``stopped_by`` says why the passes stopped. OCR is never invoked on
the web path (decision U6 is open).

ISOLATION
=========
The two ``*_worker`` functions are what ``pool.ExtractionPool`` runs in a separate,
killable process (20 s HTML, 120 s PDF). They never raise: every failure is a status
and a code. Nothing here logs page text.
"""

from __future__ import annotations

import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

#: Stamped on every web version (``research_document_versions.web_extractor_version``).
#: Independent of ``CURRENT_EXTRACTION_PIPELINE_VERSION``: bump it when THIS module's
#: reading of a page changes.
WEB_EXTRACTOR_VERSION = 1

MAX_HTML_INPUT_BYTES = 3_000_000
MIN_MAIN_TEXT_CHARS = 300
MAX_MAIN_TEXT_CHARS = 400_000
MAX_HIDDEN_TEXT_CHARS = 20_000
MAX_HEADINGS = 60
MAX_JSONLD_BLOCKS = 20
MAX_JSONLD_CHARS = 262_144

PDF_TEXT_PASS_MAX_PAGES = 400
PDF_LAYOUT_PAGES = {"standard": 25, "deep": 60}
PDF_SUPPLEMENTAL_PAGES = 12
#: Share of the PDF budget the cheap text pass may use before the layout pass starts.
_TEXT_PASS_BUDGET_SHARE = 0.45

STATUS_EXTRACTED = "extracted"
STATUS_METADATA_ONLY = "metadata_only"
STATUS_FAILED = "extraction_failed"

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

METHOD_TRAFILATURA = "trafilatura"
METHOD_STDLIB_FALLBACK = "stdlib_fallback"
METHOD_PDF = "pdf"
METHOD_PDF_TWO_PASS = "pdf_two_pass"
METHOD_TEXT = "text"

FAILURE_JS_REQUIRED = "js_required"
FAILURE_EMPTY = "empty_extraction"
FAILURE_SVG_DROPPED = "svg_dropped"
FAILURE_XML_REFUSED = "xml_refused"
FAILURE_UNSUPPORTED = "unsupported_type"
FAILURE_PARSE_ERROR = "parse_error"
FAILURE_TIMEOUT = "extraction_timeout"
FAILURE_CRASHED = "extraction_crashed"

DATE_SOURCE_JSON_LD = "json_ld"
DATE_SOURCE_META = "meta"
DATE_SOURCE_URL = "url"
DATE_SOURCE_TEXT = "text"

STOPPED_COMPLETE = "complete"
STOPPED_PAGE_CAP = "page_cap"
STOPPED_DEADLINE = "deadline"

#: Page-scoring vocabulary for the PDF text pass: what an investment question about a
#: market or a company usually needs. Scored below the run's own query terms.
INTENT_TERMS: tuple[str, ...] = (
    "market", "capacity", "demand", "supply", "forecast", "outlook", "production",
    "revenue", "price", "prices", "growth", "cost", "costs", "share", "shortage",
    "lead time", "lead times", "backlog", "investment", "pipeline", "exports", "imports",
)
_KEY_SECTION_TERMS: tuple[str, ...] = (
    "executive summary", "key findings", "key takeaways", "summary", "conclusion",
    "conclusions", "highlights", "zusammenfassung", "synthèse", "résumé",
)
_TOC_TERMS: tuple[str, ...] = ("table of contents", "contents", "inhalt", "sommaire")
_TOC_LINE_RE = re.compile(r"^(?P<title>\S.{3,120}?)[\s.·…_-]{2,}(?P<page>\d{1,3})\s*$")

_DATE_META_KEYS: tuple[str, ...] = (
    "article:published_time",
    "og:article:published_time",
    "citation_publication_date",
    "citation_date",
    "citation_online_date",
    "dc.date.issued",
    "dcterms.issued",
    "dc.date",
    "dcterms.date",
    "dcterms.created",
    "date",
    "pubdate",
    "publishdate",
    "publish-date",
    "publication_date",
    "parsely-pub-date",
    "sailthru.date",
)
_URL_DATE_RE = re.compile(
    r"/((?:19|20)\d{2})[/-](0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])(?:/|-|$)"
)
_ISO_DATE_RE = re.compile(r"((?:19|20)\d{2})-(\d{2})-(\d{2})")

_HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*(?:0(?:\.\d+)?|1(?:\.0+)?)(?:px|pt|em|rem|%)?\s*(?:;|!|$)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|$)"
    r"|(?:^|;)\s*(?:width|height)\s*:\s*0(?:px)?\s*(?:;|!|$)"
    r"|(?:left|top|text-indent|margin-left)\s*:\s*-\d{3,}(?:px|em|rem)?"
    r"|color\s*:\s*transparent"
    r"|clip\s*:\s*rect\(\s*0",
    re.IGNORECASE,
)
_HIDDEN_CLASS_RE = re.compile(
    r"(?:^|\s)(?:sr-only|visually-hidden|screen-reader-text|screenreader|hidden|d-none|"
    r"is-hidden|u-hidden|invisible)(?:\s|$)",
    re.IGNORECASE,
)
_DROP_TAGS = frozenset(
    {"script", "style", "template", "noscript", "svg", "math", "iframe", "object",
     "embed", "canvas", "form", "button", "select", "input", "textarea"}
)
_XML_ENTITY_RE = re.compile(rb"<!(?:DOCTYPE[^>]*\[|ENTITY)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Result shapes (picklable: they cross the process boundary)
# --------------------------------------------------------------------------- #


@dataclass
class WebPageMetadata:
    title: str | None = None
    published_at: date | None = None
    published_at_source: str | None = None
    author: str | None = None
    sitename: str | None = None
    og_type: str | None = None
    jsonld_types: tuple[str, ...] = ()
    declared_canonical: str | None = None
    language: str | None = None
    language_source: str | None = None
    headings: tuple[str, ...] = ()
    has_citation_meta: bool = False
    licence_signals: tuple[str, ...] = ()
    description: str | None = None


@dataclass
class WebExtraction:
    """One document's extraction. ``extraction`` is the corpus-bridge shape."""

    status: str
    method: str
    content_class: str
    failure_code: str | None = None
    confidence: str = CONFIDENCE_HIGH
    extraction: Any = None  # PrimaryDocumentExtraction
    metadata: WebPageMetadata = field(default_factory=WebPageMetadata)
    #: The visible main text, bounded — for classification, entities and SimHash.
    main_text: str = ""
    #: Text removed as hidden (bounded). A taint signal only; never a chunk.
    hidden_text: str = ""
    #: PDF document-info fields (title/subject/keywords/author), for taint scoring.
    document_info_text: str = ""
    stopped_by: str | None = None
    page_count: int | None = None
    pages_text_pass: int = 0
    pages_layout_pass: int = 0
    selected_pages: tuple[int, ...] = ()
    warnings: list[str] = field(default_factory=list)
    # -- analysis, computed IN THE WORKER so no regex or hashing pass over up to
    #    400k characters runs on the event loop (review F8) ------------------------
    simhash: int | None = None
    injection_score: float = 0.0
    injection_suspect: bool = False
    injection_signals: tuple[str, ...] = ()
    #: ``entities.Mention`` values for ``ExtractionJob.candidates``.
    mentions: list[Any] = field(default_factory=list)

    @property
    def extracted(self) -> bool:
        return self.status == STATUS_EXTRACTED and self.extraction is not None


@dataclass(frozen=True)
class ExtractionJob:
    """Everything a worker needs. Plain values only — it is pickled."""

    raw: bytes
    content_class: str
    url: str | None = None
    charset: str | None = None
    content_language: str | None = None
    js_required: bool = False
    query_terms: tuple[str, ...] = ()
    depth: str = "standard"
    budget_seconds: float = 20.0
    #: ``Settings`` overrides applied inside the worker (caps only, never secrets).
    overrides: tuple[tuple[str, Any], ...] = ()
    #: ``entities.CandidateEntity`` values to detect mentions of (plain, picklable).
    candidates: tuple[Any, ...] = ()


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #


def _collapse(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _worker_settings(overrides: tuple[tuple[str, Any], ...]) -> Any:
    from app.core.config import settings

    return settings.model_copy(update=dict(overrides)) if overrides else settings


def _parse_date(value: Any) -> date | None:
    """A publication date from an ISO-ish string, or None. Never 'today' by default."""
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value).strip()
    match = _ISO_DATE_RE.search(text)
    if not match:
        return None
    try:
        found = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return found if _plausible(found) else None


def _plausible(found: date) -> bool:
    today = datetime.now(timezone.utc).date()
    return date(1990, 1, 1) <= found <= today + timedelta(days=2)


def normalise_language(code: str | None) -> str | None:
    """``de-DE`` → ``de``; anything that is not a 2–3 letter tag → None."""
    head = re.split(r"[-_,;\s]", (code or "").strip().lower(), maxsplit=1)[0]
    return head if re.fullmatch(r"[a-z]{2,3}", head or "") else None


def _decide_language(
    html_lang: str | None, content_language: str | None, text: str
) -> tuple[str, str]:
    from app.services.sources.language import (
        detect_language_with_confidence,
        script_language,
    )

    for code, source in ((html_lang, "html_lang"), (content_language, "content_language")):
        norm = normalise_language(code)
        if norm:
            return norm, source
    script = script_language(text)
    if script:
        return script, "script"
    code, confident = detect_language_with_confidence(text)
    return code, ("content" if confident else "default")


# --------------------------------------------------------------------------- #
# HTML: tree, hidden content, metadata
# --------------------------------------------------------------------------- #


def _parse_tree(text: str) -> Any:
    from lxml import html as lxml_html

    parser = lxml_html.HTMLParser(
        huge_tree=False,
        no_network=True,
        remove_pis=True,
        recover=True,
        remove_comments=False,
    )
    return lxml_html.document_fromstring(text, parser=parser)


_CSS_RULE_RE = re.compile(r"([^{}]{1,500})\{([^{}]{0,2000})\}")
_SIMPLE_SELECTOR_RE = re.compile(r"^[a-z0-9]*([.#])([a-z0-9_-]{1,100})$", re.IGNORECASE)
_COLOR_RE = re.compile(r"(?:^|[;\s])color\s*:\s*([^;!]+)", re.IGNORECASE)
_BACKGROUND_RE = re.compile(r"background(?:-color)?\s*:\s*([^;!]+)", re.IGNORECASE)
_NAMED_COLOURS = {"white": "#ffffff", "black": "#000000"}
MAX_STYLESHEET_CHARS = 262_144


def _colour(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    text = _NAMED_COLOURS.get(text, text)
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    rgb = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)[^)]*\)", text)
    if rgb:
        text = "#" + "".join(f"{min(255, int(v)):02x}" for v in rgb.groups())
    return text or None


def _declarations_hide(style: str) -> bool:
    """Inline/stylesheet declarations that make text invisible to a sighted reader."""
    if _HIDDEN_STYLE_RE.search(style):
        return True
    fg = _COLOR_RE.search(style)
    bg = _BACKGROUND_RE.search(style)
    if fg and bg:
        colour = _colour(fg.group(1))
        return colour is not None and colour == _colour(bg.group(1).split()[0])
    return False


def stylesheet_hidden_selectors(tree: Any) -> tuple[frozenset[str], frozenset[str]]:
    """``(classes, ids)`` that ``<style>`` blocks hide with SIMPLE selectors.

    Only ``.cls``, ``#id``, ``tag.cls``, ``tag#id`` (comma lists split): enough for the
    common hidden-instruction trick (review S-M2) without a CSS engine. Complex
    selectors are ignored — a miss there is still a taint-scoring input elsewhere.
    """
    classes: set[str] = set()
    ids: set[str] = set()
    budget = MAX_STYLESHEET_CHARS
    for style in tree.iter("style"):
        css = (style.text or "")[:budget]
        budget -= len(css)
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for selectors, body in _CSS_RULE_RE.findall(css):
            if not _declarations_hide(body):
                continue
            for selector in selectors.split(","):
                match = _SIMPLE_SELECTOR_RE.match(selector.strip())
                if match:
                    (classes if match.group(1) == "." else ids).add(match.group(2).lower())
        if budget <= 0:
            break
    return frozenset(classes), frozenset(ids)


def _is_hidden(
    el: Any,
    hidden_classes: frozenset[str] = frozenset(),
    hidden_ids: frozenset[str] = frozenset(),
) -> bool:
    attrs = el.attrib
    if "hidden" in attrs:
        return True
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if (attrs.get("type") or "").strip().lower() == "hidden":
        return True
    style = attrs.get("style") or ""
    if style and _declarations_hide(style):
        return True
    classes = attrs.get("class") or ""
    if classes and _HIDDEN_CLASS_RE.search(classes):
        return True
    if hidden_classes and classes and any(
        c.lower() in hidden_classes for c in classes.split()
    ):
        return True
    element_id = (attrs.get("id") or "").strip().lower()
    return bool(element_id and element_id in hidden_ids)


def remove_hidden(tree: Any) -> str:
    """Drop hidden elements and comments IN PLACE; return their text (bounded).

    ``drop_tree`` keeps an element's tail text, which belongs to the visible parent.
    """
    from lxml import etree

    hidden_parts: list[str] = []
    budget = MAX_HIDDEN_TEXT_CHARS
    doomed: list[Any] = []
    # Read BEFORE ``<style>`` elements are dropped below.
    hidden_classes, hidden_ids = stylesheet_hidden_selectors(tree)
    for el in tree.iter():
        if el is tree:
            continue
        if isinstance(el, etree._Comment):
            doomed.append(el)
            if budget > 0:
                part = _collapse(el.text)[:budget]
                hidden_parts.append(part)
                budget -= len(part)
            continue
        if not isinstance(el.tag, str):
            doomed.append(el)
            continue
        tag = el.tag.lower()
        if tag in _DROP_TAGS:
            doomed.append(el)
            continue
        if _is_hidden(el, hidden_classes, hidden_ids):
            doomed.append(el)
            if budget > 0:
                part = _collapse(el.text_content())[:budget]
                hidden_parts.append(part)
                budget -= len(part)
    for el in doomed:
        parent = el.getparent()
        if parent is None:
            continue
        # An ancestor may already have been dropped; ``drop_tree`` on a detached
        # subtree is harmless.
        try:
            el.drop_tree()
        except (AttributeError, ValueError):
            parent.remove(el)
    return " ".join(p for p in hidden_parts if p)[:MAX_HIDDEN_TEXT_CHARS]


def _jsonld_objects(tree: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in tree.xpath('//script[@type="application/ld+json"]')[:MAX_JSONLD_BLOCKS]:
        raw = (script.text or "")[:MAX_JSONLD_CHARS]
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        stack: list[Any] = [data]
        while stack and len(objects) < 200:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item[:50])
            elif isinstance(item, dict):
                objects.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph[:50])
    return objects


def _types_of(obj: dict[str, Any]) -> list[str]:
    value = obj.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _author_of(obj: dict[str, Any]) -> str | None:
    author = obj.get("author")
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get("name")
    return _collapse(str(author))[:200] if isinstance(author, str) and author.strip() else None


_ARTICLE_TYPES = frozenset({
    "article", "newsarticle", "reportagenewsarticle", "analysisnewsarticle",
    "backgroundnewsarticle", "blogposting", "scholarlyarticle", "report", "techarticle",
    "pressrelease", "webpage",
})


def _norm_url(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("@id") or value.get("url")
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(www\.)?", "", text)
    return text.split("#", 1)[0].rstrip("/")


def _head_canonical(tree: Any) -> str | None:
    for link in tree.xpath("//link[@rel]")[:200]:
        rel = (link.get("rel") or "").lower().split()
        href = (link.get("href") or "").strip()
        if "canonical" in rel and href:
            return href[:2000]
    return None


def main_entity(
    objects: list[dict[str, Any]], *, url: str | None, canonical: str | None
) -> dict[str, Any] | None:
    """The JSON-LD object describing THIS page (review F9).

    A page often carries related-article objects with their own dates. In order: an
    article-like object whose ``url`` / ``mainEntityOfPage`` / ``@id`` is this page;
    else the first article-like object; else the only dated object; else none.
    """
    page = {u for u in (_norm_url(url), _norm_url(canonical)) if u}
    articles = [
        o for o in objects if {t.lower() for t in _types_of(o)} & _ARTICLE_TYPES
    ]
    for obj in articles:
        ids = {_norm_url(obj.get(k)) for k in ("url", "mainEntityOfPage", "@id")}
        if page & ids:
            return obj
    dated_articles = [o for o in articles if o.get("datePublished") or o.get("dateCreated")]
    if dated_articles:
        return dated_articles[0]
    dated = [o for o in objects if o.get("datePublished") or o.get("dateCreated")]
    return dated[0] if len(dated) == 1 else (articles[0] if articles else None)


def _meta_map(tree: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for meta in tree.xpath(".//meta")[:400]:
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").strip()
        content = meta.get("content")
        if key and content is not None and key.lower() not in out:
            out[key.lower()] = content.strip()[:2000]
    return out


def page_metadata(
    tree: Any, *, url: str | None, content_language: str | None
) -> WebPageMetadata:
    """Metadata from the ORIGINAL tree (meta tags and JSON-LD live in the head)."""
    meta = _meta_map(tree)
    objects = _jsonld_objects(tree)
    jsonld_types: list[str] = []
    for obj in objects:
        for t in _types_of(obj):
            if t not in jsonld_types:
                jsonld_types.append(t[:60])

    published: date | None = None
    source: str | None = None
    main = main_entity(objects, url=url, canonical=_head_canonical(tree))
    if main is not None:
        found = _parse_date(main.get("datePublished") or main.get("dateCreated"))
        if found:
            published, source = found, DATE_SOURCE_JSON_LD
    if published is None:
        for key in _DATE_META_KEYS:
            found = _parse_date(meta.get(key))
            if found:
                published, source = found, DATE_SOURCE_META
                break
    if published is None and url:
        match = _URL_DATE_RE.search(url)
        if match:
            try:
                found_url = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                found_url = None
            if found_url and _plausible(found_url):
                published, source = found_url, DATE_SOURCE_URL

    author = (_author_of(main) if main is not None else None) or (
        _collapse(meta.get("author"))[:200] or None
    )
    title_el = tree.find(".//title")
    title = _collapse(title_el.text_content() if title_el is not None else "")[:500] or None
    canonical = _head_canonical(tree)
    # Licence signals come from the document HEAD and the page's main JSON-LD entity
    # only: an ``a[rel=license]`` in the body is as often an image credit as the page's
    # own licence, and body markup can be hidden (review S-L4).
    licence: list[str] = []
    for link in tree.xpath("/html/head/link[@rel]")[:200]:
        rel = (link.get("rel") or "").lower().split()
        href = (link.get("href") or "").strip()
        if "license" in rel and href:
            licence.append(href[:300])
    head_meta = {}
    for head in tree.xpath("/html/head")[:1]:
        head_meta = _meta_map(head)
    for key in ("dc.rights", "dcterms.rights", "dcterms.license", "license"):
        if head_meta.get(key):
            licence.append(head_meta[key][:300])
    lic = main.get("license") if main is not None else None
    if isinstance(lic, str) and lic.strip():
        licence.append(lic.strip()[:300])

    html_lang = tree.get("lang") or tree.get("{http://www.w3.org/XML/1998/namespace}lang")
    return WebPageMetadata(
        title=title or (_collapse(meta.get("og:title"))[:500] or None),
        published_at=published,
        published_at_source=source,
        author=author,
        sitename=_collapse(meta.get("og:site_name"))[:200] or None,
        og_type=_collapse(meta.get("og:type"))[:60] or None,
        jsonld_types=tuple(jsonld_types[:20]),
        declared_canonical=canonical,
        language=normalise_language(html_lang) or normalise_language(content_language),
        language_source=(
            "html_lang" if normalise_language(html_lang)
            else ("content_language" if normalise_language(content_language) else None)
        ),
        has_citation_meta=any(k.startswith("citation_") for k in meta),
        licence_signals=tuple(dict.fromkeys(licence))[:10],
        description=_collapse(meta.get("description") or meta.get("og:description"))[:500]
        or None,
    )


# --------------------------------------------------------------------------- #
# HTML: trafilatura body → blocks and tables
# --------------------------------------------------------------------------- #


def _heading_level(el: Any) -> int:
    rend = (el.get("rend") or "").lower()
    if len(rend) == 2 and rend[0] == "h" and rend[1].isdigit():
        return int(rend[1])
    return 2


def body_to_blocks(body: Any) -> tuple[list[tuple[int | None, str | None, str | None, str]],
                                       list[list[list[str]]], list[str]]:
    """``(blocks, tables, headings)`` from trafilatura's XML body, in reading order."""
    blocks: list[tuple[int | None, str | None, str | None, str]] = []
    tables: list[list[list[str]]] = []
    headings: list[str] = []
    stack: list[tuple[int, str]] = []
    state: dict[str, str | None] = {"section": None, "ancestor": None}

    def _text(el: Any) -> str:
        return _collapse(" ".join(el.itertext()))

    def _walk(parent: Any) -> None:
        for child in parent:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag == "head":
                text = _text(child)
                if not text:
                    continue
                level = _heading_level(child)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                state["ancestor"] = stack[-1][1] if stack else None
                state["section"] = text[:120]
                stack.append((level, text[:120]))
                if len(headings) < MAX_HEADINGS:
                    headings.append(text[:200])
                blocks.append((None, text[:120], state["ancestor"], text))
            elif tag == "table":
                rows: list[list[str]] = []
                for row in child.iter("row"):
                    cells = [_text(cell) for cell in row.iter("cell")]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    tables.append(rows)
            elif tag == "list":
                for item in child.iter("item"):
                    text = _text(item)
                    if text:
                        blocks.append((None, state["section"], state["ancestor"], text))
            elif tag in ("div", "section", "article", "main"):
                _walk(child)
            else:
                text = _text(child)
                if text:
                    blocks.append((None, state["section"], state["ancestor"], text))

    if body is not None:
        _walk(body)
    return blocks, tables, headings


def _trafilatura_extract(tree: Any, url: str | None) -> Any:
    from trafilatura import bare_extraction
    from trafilatura.settings import use_config

    config = use_config()
    # The signal-based per-document timeout is pointless inside a killable worker.
    config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    return bare_extraction(
        tree,
        url=url,
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        with_metadata=True,
        deduplicate=False,
        config=config,
    )


def _text_date(tree: Any) -> date | None:
    """htmldate's content search, on OUR tree (never a URL — it would fetch one)."""
    try:
        from htmldate import find_date

        found = find_date(
            tree,
            extensive_search=True,
            original_date=True,
            outputformat="%Y-%m-%d",
        )
    except Exception:  # noqa: BLE001 - a date heuristic must never fail extraction
        return None
    return _parse_date(found)


def _build_extraction(
    raw: bytes,
    *,
    mime_type: str,
    method: str,
    blocks: list[tuple[int | None, str | None, str | None, str]],
    tables: list[Any],
    language: str,
    page_count: int | None,
    truncated: bool,
    cfg: Any,
) -> Any:
    from app.services.sources import primary_document_extractor as pde

    extraction = pde.PrimaryDocumentExtraction(
        content_hash=pde.content_hash_of(raw),
        mime_type=mime_type,
        extraction_method=method,
        status=pde.STATUS_EXTRACTION_FAILED,
        page_count=page_count,
        language=language,
        requires_translation=language != "en",
        truncated=truncated,
    )
    limit = pde._corpus_max_page_chars(cfg)
    extraction.blocks = [
        pde.ExtractedBlock(page_number=p, section=s, ancestor_heading=a, text=t[:limit])
        for p, s, a, t in blocks
    ]
    extraction.tables = list(tables)
    extraction.excerpts = pde._rank_and_build_excerpts(
        blocks,
        method=method,
        max_excerpts=max(1, int(cfg.primary_document_max_excerpts_per_document)),
        per_excerpt=max(120, int(cfg.primary_document_max_excerpt_chars)),
    )
    extraction.extracted_char_count = sum(len(b[3]) for b in blocks)
    if extraction.blocks or extraction.tables:
        extraction.status = pde.STATUS_EXTRACTED
    else:
        extraction.status = pde.STATUS_METADATA_ONLY
        extraction.failure_code = pde.FAILURE_EMPTY_EXTRACTION
    return extraction


def _html_tables(raw_tables: list[list[list[str]]]) -> list[Any]:
    from app.services.sources import primary_document_extractor as pde

    out: list[Any] = []
    for index, rows in enumerate(raw_tables):
        bounded = pde._bound_table(rows)
        if not bounded:
            continue
        out.append(
            pde.ExtractedTable(
                table_location=f"t{index}",
                table_index=index,
                page_number=None,
                rows=bounded,
                row_count=len(bounded),
                col_count=max(len(r) for r in bounded),
                extraction_method=pde.METHOD_HTML,
                # A web page's heading never makes a table a Group figure.
                scope=None,
            )
        )
    return out


def analyse(result: WebExtraction, job: ExtractionJob) -> WebExtraction:
    """SimHash, injection taint and entity mentions — the CPU-heavy reads of the text.

    Runs inside the worker (review F8). Never raises: a failed analysis leaves the
    defaults, which are the conservative ones (no fingerprint, no mention).
    """
    try:
        from app.services.web_research.classify import injection_assessment
        from app.services.web_research.dedup import simhash64, to_signed64

        meta = result.metadata
        taint = injection_assessment(
            result.main_text,
            hidden_text=result.hidden_text,
            metadata_text=" ".join(
                part
                for part in (meta.title, meta.description, result.document_info_text)
                if part
            ),
        )
        result.injection_score = taint.score
        result.injection_suspect = taint.suspect
        result.injection_signals = taint.signals
        result.simhash = to_signed64(simhash64(result.main_text))
        if job.candidates and result.main_text:
            from urllib.parse import urlsplit

            from app.services.web_research.entities import detect_mentions

            host = (urlsplit(job.url or "").hostname or "").lower() or None
            result.mentions = detect_mentions(
                result.main_text, list(job.candidates), page_host=host
            )
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"analysis failed: {type(exc).__name__}")
    return result


def extract_html_worker(job: ExtractionJob) -> WebExtraction:
    """HTML → :class:`WebExtraction`. Runs in the pool; never raises."""
    try:
        return analyse(_extract_html(job), job)
    except Exception as exc:  # noqa: BLE001 - a hostile page must never crash a worker
        return WebExtraction(
            status=STATUS_FAILED,
            method=METHOD_TRAFILATURA,
            content_class=job.content_class,
            failure_code=FAILURE_PARSE_ERROR,
            warnings=[type(exc).__name__],
        )


def _refuse_markup(raw: bytes, content_class: str) -> WebExtraction | None:
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return WebExtraction(STATUS_FAILED, METHOD_TRAFILATURA, content_class,
                             failure_code=FAILURE_SVG_DROPPED)
    if _XML_ENTITY_RE.search(raw[:65536]):
        return WebExtraction(STATUS_FAILED, METHOD_TRAFILATURA, content_class,
                             failure_code=FAILURE_XML_REFUSED)
    if head.startswith(b"<?xml") and b"<html" not in head:
        return WebExtraction(STATUS_FAILED, METHOD_TRAFILATURA, content_class,
                             failure_code=FAILURE_XML_REFUSED)
    return None


def _extract_html(job: ExtractionJob) -> WebExtraction:
    from lxml import html as lxml_html

    from app.services.sources import primary_document_extractor as pde
    from app.services.web_research.content import decode_text

    cfg = _worker_settings(job.overrides)
    raw = job.raw[:MAX_HTML_INPUT_BYTES]
    truncated = len(job.raw) > MAX_HTML_INPUT_BYTES
    refused = _refuse_markup(raw, job.content_class)
    if refused is not None:
        return refused

    text = decode_text(raw, job.charset or "utf-8")
    try:
        tree = _parse_tree(text)
    except Exception:  # noqa: BLE001 - lxml refuses an empty/unparseable document
        return WebExtraction(STATUS_METADATA_ONLY, METHOD_TRAFILATURA, job.content_class,
                             failure_code=FAILURE_EMPTY)
    metadata = page_metadata(tree, url=job.url, content_language=job.content_language)
    hidden = remove_hidden(tree)
    visible_html = lxml_html.tostring(tree, encoding="utf-8")

    doc = None
    try:
        doc = _trafilatura_extract(tree, job.url)
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail
        doc = None
        warning = f"trafilatura failed: {type(exc).__name__}"
    else:
        warning = ""
    blocks: list[tuple[int | None, str | None, str | None, str]] = []
    tables: list[Any] = []
    headings: list[str] = []
    main = ""
    if doc is not None:
        blocks, raw_tables, headings = body_to_blocks(getattr(doc, "body", None))
        tables = _html_tables(raw_tables)
        main = "\n".join(b[3] for b in blocks)
    method = METHOD_TRAFILATURA
    confidence = CONFIDENCE_HIGH
    if len(main) < MIN_MAIN_TEXT_CHARS:
        fallback = pde.extract_html(visible_html, cfg=cfg, capture_blocks=True)
        fb_blocks = [
            (b.page_number, b.section, b.ancestor_heading, b.text) for b in fallback.blocks
        ]
        fb_main = "\n".join(b[3] for b in fb_blocks)
        if len(fb_main) > len(main):
            blocks, tables, main = fb_blocks, list(fallback.tables), fb_main
            headings = headings or [b[1] for b in fb_blocks if b[1] and b[1] == b[3]][:MAX_HEADINGS]
            method = METHOD_STDLIB_FALLBACK
        confidence = CONFIDENCE_LOW

    if doc is not None:
        if not metadata.title and getattr(doc, "title", None):
            metadata.title = _collapse(doc.title)[:500]
        if not metadata.author and getattr(doc, "author", None):
            metadata.author = _collapse(doc.author)[:200]
        if not metadata.sitename and getattr(doc, "sitename", None):
            metadata.sitename = _collapse(doc.sitename)[:200]
    if metadata.published_at is None and main:
        found = _text_date(tree)
        if found:
            metadata.published_at, metadata.published_at_source = found, DATE_SOURCE_TEXT
    metadata.headings = tuple(headings[:MAX_HEADINGS])
    language, language_source = _decide_language(
        metadata.language, job.content_language, main
    )
    metadata.language, metadata.language_source = language, language_source

    result = WebExtraction(
        status=STATUS_METADATA_ONLY,
        method=method,
        content_class=job.content_class,
        confidence=confidence,
        metadata=metadata,
        main_text=main[:MAX_MAIN_TEXT_CHARS],
        hidden_text=hidden,
        warnings=[warning] if warning else [],
    )
    if not main.strip() and not tables:
        # A JS-only shell (or an empty page): nothing is invented.
        result.failure_code = FAILURE_JS_REQUIRED if job.js_required else FAILURE_EMPTY
        return result
    if job.js_required and len(main) < MIN_MAIN_TEXT_CHARS:
        # A shell with a nav bar and "Loading…" is not the document (review S-L2).
        result.failure_code = FAILURE_JS_REQUIRED
        return result
    result.extraction = _build_extraction(
        job.raw,
        mime_type="text/html",
        method=pde.METHOD_HTML,
        blocks=blocks,
        tables=tables,
        language=language,
        page_count=None,
        truncated=truncated,
        cfg=cfg,
    )
    result.status = result.extraction.status
    result.failure_code = result.extraction.failure_code
    return result


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #


def extract_text_document(job: ExtractionJob) -> WebExtraction:
    """A ``text/plain`` body → paragraphs. JSON and XML are not documents here."""
    return analyse(_extract_text(job), job)


def _extract_text(job: ExtractionJob) -> WebExtraction:
    from app.services.sources import primary_document_extractor as pde
    from app.services.web_research.content import decode_text

    refused = _refuse_markup(job.raw, job.content_class)
    if refused is not None:
        return refused
    head = job.raw[:64].lstrip()
    if head[:1] in (b"{", b"["):
        return WebExtraction(STATUS_FAILED, METHOD_TEXT, job.content_class,
                             failure_code=FAILURE_UNSUPPORTED)
    cfg = _worker_settings(job.overrides)
    text = decode_text(job.raw, job.charset or "utf-8")
    paragraphs = [_collapse(p) for p in re.split(r"\n\s*\n", text)]
    blocks: list[tuple[int | None, str | None, str | None, str]] = [
        (None, None, None, p) for p in paragraphs if p
    ]
    main = "\n".join(b[3] for b in blocks)
    language, source = _decide_language(None, job.content_language, main)
    metadata = WebPageMetadata(language=language, language_source=source)
    if not blocks:
        return WebExtraction(STATUS_METADATA_ONLY, METHOD_TEXT, job.content_class,
                             failure_code=FAILURE_EMPTY, metadata=metadata)
    extraction = _build_extraction(
        job.raw, mime_type="text/plain", method=pde.METHOD_HTML, blocks=blocks, tables=[],
        language=language, page_count=None, truncated=False, cfg=cfg,
    )
    return WebExtraction(
        status=extraction.status, method=METHOD_TEXT, content_class=job.content_class,
        extraction=extraction, metadata=metadata, main_text=main[:MAX_MAIN_TEXT_CHARS],
        confidence=CONFIDENCE_LOW,
    )


# --------------------------------------------------------------------------- #
# PDF: single pass or two-pass large-document mode
# --------------------------------------------------------------------------- #


def query_term_list(terms: tuple[str, ...]) -> list[str]:
    """Lower-cased, accent-folded, de-duplicated scoring terms (≥ 3 characters)."""
    out: list[str] = []
    for term in terms:
        folded = _fold(term)
        for token in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", folded):
            if token not in out:
                out.append(token)
        phrase = _collapse(folded)
        if " " in phrase and phrase not in out and len(phrase) <= 80:
            out.append(phrase)
    return out[:40]


def _fold(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in raw if not unicodedata.combining(ch))


def score_pages(
    page_texts: dict[int, str],
    query_terms: tuple[str, ...],
    *,
    label_to_page: dict[str, int] | None = None,
) -> dict[int, float]:
    """Deterministic page scores for the layout pass (spec §10.2, pass 1).

    Query terms weigh most (capped per term, so one repeated word cannot dominate),
    then the intent vocabulary, then key sections (executive summary, conclusions);
    a TOC line naming a query term boosts the page it points at.
    """
    terms = query_term_list(query_terms)
    scores: dict[int, float] = {}
    toc_boost: dict[int, float] = {}
    for page_no, text in page_texts.items():
        folded = _fold(text)
        score = 0.0
        for term in terms:
            hits = len(re.findall(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", folded))
            score += 3.0 * min(hits, 5)
        for term in INTENT_TERMS:
            if term in folded:
                score += 0.5
        head = folded[:600]
        if any(term in head for term in _KEY_SECTION_TERMS):
            score += 4.0
        if any(term in head for term in _TOC_TERMS):
            for line in text.splitlines()[:200]:
                match = _TOC_LINE_RE.match(line.strip())
                if not match:
                    continue
                title = _fold(match.group("title"))
                printed = match.group("page")
                # A TOC prints PAGE LABELS ("23"), not PDF indices: map through the
                # document's own page labels when it has them (review F11).
                target = (label_to_page or {}).get(printed) or int(printed)
                if terms and any(t in title for t in terms):
                    toc_boost[target] = toc_boost.get(target, 0.0) + 5.0
        scores[page_no] = score
    for page_no, boost in toc_boost.items():
        if page_no in scores:
            scores[page_no] += boost
    return scores


def select_layout_pages(scores: dict[int, float], limit: int) -> list[int]:
    """The top ``limit`` positive-scoring pages, ties to the earlier page; page 1 always."""
    if limit <= 0:
        return []
    ranked = sorted((p for p, s in scores.items() if s > 0), key=lambda p: (-scores[p], p))
    chosen = ranked[:limit]
    if limit >= 2 and 1 in scores and 1 not in chosen:
        # The cover/abstract page anchors the document's title and date — but never at
        # the cost of the ONLY page a one-page budget allows (review F11).
        chosen = [*chosen[: limit - 1], 1]
    return sorted(set(chosen))


Block = tuple[int | None, str | None, str | None, str]


def _paragraph_blocks(page_no: int, text: str) -> list[Block]:
    parts = [_collapse(p) for p in re.split(r"\n\s*\n", text or "")]
    parts = [p for p in parts if p]
    if not parts and (text or "").strip():
        parts = [_collapse(text)]
    return [(page_no, None, None, p) for p in parts]


def extract_pdf_worker(job: ExtractionJob) -> WebExtraction:
    """PDF → :class:`WebExtraction`. Runs in the pool; never raises."""
    try:
        return analyse(_extract_pdf(job), job)
    except Exception as exc:  # noqa: BLE001
        return WebExtraction(
            status=STATUS_FAILED, method=METHOD_PDF, content_class=job.content_class,
            failure_code=FAILURE_PARSE_ERROR, warnings=[type(exc).__name__],
        )


DATE_SOURCE_PDF_METADATA = "pdf_metadata"
_MONTHS = {m: i for i, m in enumerate(
    ("january february march april may june july august september october november "
     "december").split(), start=1)}
_TEXT_DATE_RES = (
    re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d{2})\b", re.I),
    re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s+((?:19|20)\d{2})\b", re.I),
    re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d{2})\b", re.I),
)


def _pdf_creation_date(reader: Any) -> date | None:
    try:
        created = reader.metadata.creation_date if reader.metadata else None
    except Exception:  # noqa: BLE001 - a malformed /CreationDate is no date
        return None
    if isinstance(created, datetime):
        found = created.date()
        return found if _plausible(found) else None
    return None


def first_page_date(text: str | None) -> date | None:
    """A date printed on the cover page ("March 2026", "4 March 2026", ISO), or None."""
    head = (text or "")[:3000]
    for index, pattern in enumerate(_TEXT_DATE_RES):
        match = pattern.search(head)
        if not match:
            continue
        g = match.groups()
        try:
            if index == 0:
                found = date(int(g[0]), int(g[1]), int(g[2]))
            elif index == 1:
                found = date(int(g[2]), _MONTHS[g[1].lower()], int(g[0]))
            elif index == 2:
                found = date(int(g[2]), _MONTHS[g[0].lower()], int(g[1]))
            else:
                found = date(int(g[1]), _MONTHS[g[0].lower()], 1)
        except (ValueError, KeyError):
            continue
        if _plausible(found):
            return found
    return None


def _page_label_map(reader: Any, limit: int) -> dict[str, int]:
    """Printed page label → 1-based PDF index (review F11). Empty when unlabelled."""
    try:
        labels = list(reader.page_labels)[:limit]
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for index, label in enumerate(labels, start=1):
        if label and str(label) not in out:
            out[str(label)] = index
    return out


#: A PDF character this small (points) or this close to white is invisible on paper.
_PDF_TINY_POINTS = 1.0
_PDF_NEAR_WHITE = 0.95
MAX_PDF_HIDDEN_PAGES = 40


def _near_white(colour: Any) -> bool:
    if colour is None:
        return False
    values = colour if isinstance(colour, (list, tuple)) else (colour,)
    try:
        numbers = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    if len(numbers) == 4:  # CMYK: white is no ink
        return all(v <= 1 - _PDF_NEAR_WHITE for v in numbers)
    return bool(numbers) and all(v >= _PDF_NEAR_WHITE for v in numbers)


def pdf_hidden_text(raw: bytes, pages: list[int], deadline: float) -> str:
    """Text a reader cannot see — sub-point or near-white characters (review S-M2).

    A taint-scoring input only: the characters are not removed from the extracted text
    (the shared PDF extractor is V2's), but a document carrying instructions in them is
    flagged. Bounded by page count and the worker's deadline; never raises.
    """
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    budget = MAX_HIDDEN_TEXT_CHARS
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_no in pages[:MAX_PDF_HIDDEN_PAGES]:
                if time.monotonic() > deadline or budget <= 0:
                    break
                if not 1 <= page_no <= len(pdf.pages):
                    continue
                chars = pdf.pages[page_no - 1].chars or []
                hidden = "".join(
                    str(c.get("text", ""))
                    for c in chars
                    if float(c.get("size") or 0) < _PDF_TINY_POINTS
                    or _near_white(c.get("non_stroking_color"))
                )
                if hidden.strip():
                    part = _collapse(hidden)[:budget]
                    parts.append(part)
                    budget -= len(part)
    except Exception:  # noqa: BLE001 - a taint probe must never fail extraction
        return " ".join(parts)
    return " ".join(parts)[:MAX_HIDDEN_TEXT_CHARS]


def _pdf_info_text(reader: Any) -> tuple[str | None, str]:
    try:
        info = reader.metadata or {}
    except Exception:  # noqa: BLE001
        return None, ""
    parts: list[str] = []
    title = None
    for key in ("/Title", "/Subject", "/Keywords", "/Author", "/Creator"):
        try:
            value = info.get(key)
        except Exception:  # noqa: BLE001
            value = None
        if value:
            text = _collapse(str(value))[:1000]
            parts.append(text)
            if key == "/Title" and text:
                title = text[:300]
    return title, " ".join(parts)


def _extract_pdf(job: ExtractionJob) -> WebExtraction:
    from app.services.sources import primary_document_extractor as pde

    started = time.monotonic()
    budget = max(5.0, float(job.budget_seconds))
    cfg = _worker_settings(job.overrides)
    layout_limit = PDF_LAYOUT_PAGES.get(job.depth, PDF_LAYOUT_PAGES["standard"])

    reader = None
    page_count = 0
    info_title: str | None = None
    info_text = ""
    created: date | None = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(job.raw), strict=False)
        if reader.is_encrypted:
            reader = None  # extract_pdf classifies encryption honestly
        else:
            page_count = len(reader.pages)
            info_title, info_text = _pdf_info_text(reader)
            created = _pdf_creation_date(reader)
    except Exception:  # noqa: BLE001 - malformed: the layout extractor says why
        reader = None
    hidden_deadline = started + budget * 0.95

    if reader is None or page_count <= layout_limit:
        pass_cfg = cfg.model_copy(update={
            "primary_document_max_pdf_pages": max(1, min(page_count or layout_limit, layout_limit)),
            "primary_document_extraction_timeout_seconds": max(1, int(budget * 0.9)),
        })
        extraction = pde.extract_primary_document(job.raw, document_type="pdf", cfg=pass_cfg,
                                                  capture_blocks=True)
        timed_out = any("time budget" in w.lower() for w in extraction.warnings)
        read_pages = sorted({b.page_number for b in extraction.blocks if b.page_number})
        return _pdf_result(
            job, extraction, method=METHOD_PDF, info_title=info_title,
            info_text=info_text, page_count=extraction.page_count,
            stopped_by=STOPPED_DEADLINE if timed_out else STOPPED_COMPLETE,
            text_pages=0, layout_pages=len(read_pages),
            selected=(), created=created,
            hidden=pdf_hidden_text(job.raw, read_pages, hidden_deadline),
        )

    # Pass 1: cheap text for up to 400 pages.
    text_deadline = started + budget * _TEXT_PASS_BUDGET_SHARE
    text_limit = min(page_count, PDF_TEXT_PASS_MAX_PAGES)
    page_texts: dict[int, str] = {}
    stopped_by = STOPPED_PAGE_CAP if page_count > PDF_TEXT_PASS_MAX_PAGES else STOPPED_COMPLETE
    max_chars = pde._corpus_max_page_chars(cfg)
    for index in range(text_limit):
        if time.monotonic() > text_deadline:
            stopped_by = STOPPED_DEADLINE
            break
        try:
            page_texts[index + 1] = (reader.pages[index].extract_text() or "")[:max_chars]
        except Exception:  # noqa: BLE001 - one bad page costs that page
            page_texts[index + 1] = ""

    scores = score_pages(
        page_texts, job.query_terms, label_to_page=_page_label_map(reader, text_limit)
    )
    selected = select_layout_pages(scores, layout_limit)
    supplemental = pde._select_statement_pages(
        job.raw, total_pages=page_count, exclude=set(selected),
        max_pages=PDF_SUPPLEMENTAL_PAGES,
    )
    chosen = sorted(set(selected) | set(supplemental))

    # Pass 2: layout + tables on the chosen pages, inside what is left of the budget.
    remaining = budget - (time.monotonic() - started)
    layout_cfg = cfg.model_copy(update={
        "primary_document_extraction_timeout_seconds": max(1, int(remaining * 0.85)),
    })
    layout = pde.extract_primary_document(job.raw, document_type="pdf", cfg=layout_cfg,
                                          capture_blocks=True, pages=chosen)
    if any("time budget" in w.lower() for w in layout.warnings):
        stopped_by = STOPPED_DEADLINE
    layout_pages = {b.page_number for b in layout.blocks if b.page_number is not None}
    layout_pages |= {t.page_number for t in layout.tables if t.page_number is not None}

    merged: list[tuple[int | None, str | None, str | None, str]] = []
    layout_by_page: dict[int, list[Any]] = {}
    for block in layout.blocks:
        if block.page_number is not None:
            layout_by_page.setdefault(block.page_number, []).append(block)
    for page_no in sorted(set(page_texts) | layout_pages):
        if page_no in layout_by_page:
            merged.extend(
                (b.page_number, b.section, b.ancestor_heading, b.text)
                for b in layout_by_page[page_no]
            )
        else:
            merged.extend(_paragraph_blocks(page_no, page_texts.get(page_no, "")))

    head_text = " ".join(b[3] for b in merged[:12])
    from app.services.sources.language import detect_language, script_language

    language = script_language(head_text) or detect_language(head_text)
    extraction = _build_extraction(
        job.raw, mime_type="application/pdf", method=pde.METHOD_NATIVE_PDF, blocks=merged,
        tables=list(layout.tables), language=language, page_count=page_count,
        truncated=stopped_by != STOPPED_COMPLETE or len(page_texts) < page_count, cfg=cfg,
    )
    extraction.encrypted = layout.encrypted
    extraction.warnings = list(layout.warnings)[:50]
    return _pdf_result(job, extraction, method=METHOD_PDF_TWO_PASS, info_title=info_title,
                       info_text=info_text, page_count=page_count, stopped_by=stopped_by,
                       text_pages=len(page_texts), layout_pages=len(layout_pages),
                       selected=tuple(chosen), created=created,
                       hidden=pdf_hidden_text(job.raw, chosen, hidden_deadline))


def _pdf_result(
    job: ExtractionJob, extraction: Any, *, method: str, info_title: str | None,
    info_text: str, page_count: int | None, stopped_by: str, text_pages: int,
    layout_pages: int, selected: tuple[int, ...], created: date | None = None,
    hidden: str = "",
) -> WebExtraction:
    blocks = list(getattr(extraction, "blocks", None) or [])
    main = "\n".join(b.text for b in blocks)[:MAX_MAIN_TEXT_CHARS]
    first_line = next((_collapse(b.text)[:200] for b in blocks if _collapse(b.text)), None)
    # A PDF's own date (review F9): the cover page's printed date first — that is what
    # the publisher says — then the file's /CreationDate, each labelled with its source.
    cover = " ".join(b.text for b in blocks if b.page_number == 1)
    published = first_page_date(cover)
    source = DATE_SOURCE_TEXT if published else None
    if published is None and created is not None:
        published, source = created, DATE_SOURCE_PDF_METADATA
    metadata = WebPageMetadata(
        title=info_title or first_line,
        published_at=published,
        published_at_source=source,
        language=extraction.language,
        language_source="content",
    )
    status = extraction.status
    return WebExtraction(
        status=status,
        method=method,
        content_class=job.content_class,
        failure_code=extraction.failure_code,
        extraction=extraction if status == STATUS_EXTRACTED else None,
        metadata=metadata,
        main_text=main,
        hidden_text=hidden,
        document_info_text=info_text,
        stopped_by=stopped_by,
        page_count=page_count,
        pages_text_pass=text_pages,
        pages_layout_pass=layout_pages,
        selected_pages=selected,
        warnings=list(getattr(extraction, "warnings", []) or [])[:20],
    )


# --------------------------------------------------------------------------- #
# The async entry point
# --------------------------------------------------------------------------- #

#: Settings the worker needs relaxed for web documents (never secrets).
_WEB_OVERRIDES: tuple[tuple[str, Any], ...] = (
    # A web PDF may be up to 35 MB (the fetch cap); the 8 MB figure only sets an
    # honest "truncated" flag on the V2 path and would mark every large PDF partial.
    ("primary_document_max_download_bytes", 35_000_000),
)


async def extract_web_document(
    *,
    raw: bytes,
    content_class: str,
    cfg: Any,
    url: str | None = None,
    charset: str | None = None,
    content_language: str | None = None,
    js_required: bool = False,
    query_terms: tuple[str, ...] = (),
    depth: str = "standard",
    pool: Any = None,
    candidates: tuple[Any, ...] = (),
) -> WebExtraction:
    """Extract (and analyse) one fetched document in the isolated pool. Never raises."""
    from app.services.web_research import content as content_mod
    from app.services.web_research.pool import (
        ExtractionCrashed,
        ExtractionTimeout,
        get_extraction_pool,
    )

    if content_class == content_mod.CLASS_PDF:
        worker, timeout = extract_pdf_worker, float(
            getattr(cfg, "v3_web_pdf_extraction_timeout_seconds", 120) or 120
        )
        method = METHOD_PDF
    elif content_class == content_mod.CLASS_HTML:
        worker, timeout = extract_html_worker, float(
            getattr(cfg, "v3_web_html_extraction_timeout_seconds", 20) or 20
        )
        method = METHOD_TRAFILATURA
    elif content_class == content_mod.CLASS_TEXT:
        worker, timeout = extract_text_document, float(
            getattr(cfg, "v3_web_html_extraction_timeout_seconds", 20) or 20
        )
        method = METHOD_TEXT
    else:
        return WebExtraction(STATUS_FAILED, METHOD_TRAFILATURA, content_class,
                             failure_code=FAILURE_UNSUPPORTED)
    job = ExtractionJob(
        raw=raw,
        content_class=content_class,
        url=url,
        charset=charset,
        content_language=content_language,
        js_required=js_required,
        query_terms=tuple(query_terms)[:40],
        depth=depth if depth in PDF_LAYOUT_PAGES else "standard",
        # The cooperative budget sits inside the hard kill, so a slow document
        # usually finishes partial instead of being killed.
        budget_seconds=max(1.0, timeout * 0.9),
        overrides=_WEB_OVERRIDES,
        candidates=tuple(candidates),
    )
    runner = pool or get_extraction_pool(cfg)
    try:
        result: WebExtraction = await runner.run(worker, job, timeout=timeout)
        return result
    except ExtractionTimeout:
        return WebExtraction(STATUS_FAILED, method, content_class, failure_code=FAILURE_TIMEOUT)
    except ExtractionCrashed:
        return WebExtraction(STATUS_FAILED, method, content_class, failure_code=FAILURE_CRASHED)
    except Exception as exc:  # noqa: BLE001 - "never raises" holds for ANY pool failure
        return WebExtraction(STATUS_FAILED, method, content_class,
                             failure_code=FAILURE_CRASHED, warnings=[type(exc).__name__])


__all__ = [
    "DATE_SOURCE_JSON_LD",
    "DATE_SOURCE_META",
    "DATE_SOURCE_TEXT",
    "DATE_SOURCE_URL",
    "FAILURE_CRASHED",
    "FAILURE_JS_REQUIRED",
    "FAILURE_SVG_DROPPED",
    "FAILURE_TIMEOUT",
    "FAILURE_XML_REFUSED",
    "MIN_MAIN_TEXT_CHARS",
    "PDF_LAYOUT_PAGES",
    "STOPPED_COMPLETE",
    "STOPPED_DEADLINE",
    "STOPPED_PAGE_CAP",
    "WEB_EXTRACTOR_VERSION",
    "ExtractionJob",
    "WebExtraction",
    "WebPageMetadata",
    "extract_html_worker",
    "extract_pdf_worker",
    "extract_text_document",
    "extract_web_document",
    "page_metadata",
    "remove_hidden",
    "score_pages",
    "select_layout_pages",
]
