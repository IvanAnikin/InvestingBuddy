"""A guarded RSS / Atom item parser — V3.21.6 (ADR-113).

Two feed parsers already exist, each for its own purpose: ``document_discovery.
discover_from_feed`` finds DOCUMENT links in an issuer's feed, and
``company_press_release_provider.parse_feed`` turns an issuer's feed into T1 news items.
This one returns the plain fields of each item — title, summary, link, published date and
guid — for any caller that stores or reads feed ITEMS (the trade-press poller first).

GUARDS
======
* **XXE / entity expansion.** A document containing ``<!DOCTYPE`` or ``<!ENTITY`` is
  refused BEFORE parsing, so no entity can be defined or expanded (the same rule as
  ``discover_from_feed``; no ``defusedxml`` dependency is needed).
* **Size.** The text is capped (:data:`MAX_FEED_CHARS`), the item count is capped, and
  each field is capped.
* **Bodies are never read.** ``content:encoded``, Atom ``content`` and publisher
  full-text extensions (``wnn:fullText``) are skipped: an item's summary is its
  ``description`` / ``summary`` only, with markup removed and cut to
  :data:`SUMMARY_MAX_CHARS` on a word boundary. Some feeds put the whole article in
  ``description``; the cut is what keeps that an excerpt.
* **Links** are ``http(s)`` only; anything else is dropped.

Never raises: malformed XML returns an empty result with ``refused`` saying why.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

#: The most feed text parsed. The largest of the nine trade-press feeds was ~0.4 MB.
MAX_FEED_CHARS = 4_000_000
MAX_ITEMS = 100
TITLE_MAX_CHARS = 300
SUMMARY_MAX_CHARS = 600
LINK_MAX_CHARS = 1000
GUID_MAX_CHARS = 500
#: Raw markup read from a description before it is stripped (bounds the regex work).
_RAW_SUMMARY_SCAN = 20_000

REFUSED_EMPTY = "empty"
REFUSED_TOO_LARGE = "too_large"
REFUSED_DTD = "doctype_or_entity"
REFUSED_MALFORMED = "malformed_xml"
REFUSED_NOT_A_FEED = "not_a_feed"

_ITEM_TAGS = frozenset({"item", "entry"})
_TITLE_TAGS = frozenset({"title"})
_SUMMARY_TAGS = frozenset({"description", "summary"})
_DATE_TAGS = ("pubdate", "published", "updated", "date", "issued", "modified")
_GUID_TAGS = frozenset({"guid", "id"})

_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]{0,2000}>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str | None
    summary: str | None = None
    published_at: datetime | None = None
    guid: str | None = None

    @property
    def published_date(self) -> date | None:
        return self.published_at.date() if self.published_at else None

    @property
    def identity(self) -> str | None:
        """What dedupes the item: its guid, else its link."""
        return self.guid or self.link


@dataclass
class FeedParseResult:
    items: list[FeedItem] = field(default_factory=list)
    refused: str | None = None
    #: Items skipped for having no title or no usable link.
    skipped: int = 0


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].strip().lower()


def plain_text(value: str | None, limit: int) -> str | None:
    """Markup removed, entities decoded, whitespace collapsed, cut on a word boundary."""
    if not value:
        return None
    raw = value[:_RAW_SUMMARY_SCAN]
    raw = _SCRIPT_RE.sub(" ", raw)
    raw = _COMMENT_RE.sub(" ", raw)
    raw = _TAG_RE.sub(" ", raw)
    text = _WS_RE.sub(" ", html.unescape(raw)).strip()
    # An unescaped entity can itself be markup (``&lt;p&gt;``): strip once more.
    if "<" in text:
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def _link(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or len(text) > LINK_MAX_CHARS:
        return None
    if not text.lower().startswith(("https://", "http://")):
        return None
    if any(ch.isspace() for ch in text):
        return None
    return text


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS ``pubDate``) or ISO 8601 (Atom), as an aware UTC datetime."""
    text = (value or "").strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _item(entry: ElementTree.Element) -> FeedItem | None:
    title: str | None = None
    summary_raw: str | None = None
    rss_link: str | None = None
    atom_links: list[tuple[str, str]] = []
    guid: str | None = None
    dates: dict[str, str] = {}
    for child in list(entry)[:200]:
        tag = _local(child.tag)
        text = child.text or ""
        if tag in _TITLE_TAGS and title is None:
            title = plain_text("".join(child.itertext()), TITLE_MAX_CHARS)
        elif tag == "link":
            href = child.attrib.get("href")
            if href is not None:
                atom_links.append(((child.attrib.get("rel") or "alternate").lower(), href))
            elif rss_link is None:
                rss_link = text
        elif tag in _SUMMARY_TAGS and summary_raw is None:
            summary_raw = "".join(child.itertext())
        elif tag in _GUID_TAGS and guid is None:
            guid = text.strip()[:GUID_MAX_CHARS] or None
        elif tag in _DATE_TAGS and tag not in dates:
            dates[tag] = text
    link = _link(rss_link)
    if link is None:
        for rel, href in atom_links:
            if rel == "alternate" and _link(href):
                link = _link(href)
                break
    if link is None and guid and _link(guid):
        link = _link(guid)
    if not title or link is None:
        return None
    published = next((parse_date(dates[t]) for t in _DATE_TAGS if t in dates
                      and parse_date(dates[t]) is not None), None)
    return FeedItem(title=title, link=link,
                    summary=plain_text(summary_raw, SUMMARY_MAX_CHARS),
                    published_at=published, guid=guid)


def parse_feed_items(xml: str | bytes | None, *, max_items: int = MAX_ITEMS) -> FeedParseResult:
    """Every item of an RSS 2.0 / RSS 1.0 / Atom document, guarded. Never raises."""
    result = FeedParseResult()
    if isinstance(xml, bytes):
        if len(xml) > MAX_FEED_CHARS:
            result.refused = REFUSED_TOO_LARGE
            return result
        text = xml.decode("utf-8", errors="replace")
    else:
        text = xml or ""
    if not text.strip():
        result.refused = REFUSED_EMPTY
        return result
    if len(text) > MAX_FEED_CHARS:
        result.refused = REFUSED_TOO_LARGE
        return result
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        result.refused = REFUSED_DTD
        return result
    # The XML declaration may name an encoding; the text is already decoded.
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        result.refused = REFUSED_MALFORMED
        return result
    if _local(root.tag) not in {"rss", "feed", "rdf"}:
        result.refused = REFUSED_NOT_A_FEED
        return result
    bound = max(1, min(int(max_items), MAX_ITEMS))
    seen = 0
    for element in root.iter():
        if _local(element.tag) not in _ITEM_TAGS:
            continue
        seen += 1
        if seen > MAX_ITEMS * 2:
            break
        item = _item(element)
        if item is None:
            result.skipped += 1
            continue
        result.items.append(item)
        if len(result.items) >= bound:
            break
    return result


__all__ = [
    "MAX_FEED_CHARS",
    "MAX_ITEMS",
    "REFUSED_DTD",
    "REFUSED_EMPTY",
    "REFUSED_MALFORMED",
    "REFUSED_NOT_A_FEED",
    "REFUSED_TOO_LARGE",
    "SUMMARY_MAX_CHARS",
    "FeedItem",
    "FeedParseResult",
    "parse_date",
    "parse_feed_items",
    "plain_text",
]
