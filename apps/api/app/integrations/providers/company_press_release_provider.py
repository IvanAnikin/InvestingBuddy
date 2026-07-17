"""
Phase 24 — Company press-release / investor-relations provider.

Attempts safe, conservative discovery of a company's OWN newsroom / IR feed and
parses it into press-release NewsItems.

Source tier: T1_primary_filing.
  The project's SourceTier enum has no dedicated ``T1_primary_company_source``;
  a company-owned primary source therefore uses ``T1_primary_filing`` and is
  documented as "company-owned primary source". A press release from the issuer
  is a primary source (the company's own words), distinct from an SEC filing
  (T2) or a third-party aggregator (T5).

Safety / conservativeness:
  - Only a small set of well-known RSS/Atom paths are probed off a supplied
    website. There is NO web crawl and NO aggressive scraping.
  - Short timeouts; small result cap.
  - When no website/feed is available, returns an explicit
    "company primary news source unavailable" warning — never fabricates a
    press release.
  - ``parse_feed`` is a pure function (no network) so all CI tests run offline.

Future enhancement: honour robots.txt / <meta name="robots"> and use
``profile.website`` once identity enrichment populates it for more issuers.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx

from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import NewsItem, PressReleaseStatus

_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"

# Conservative set of common newsroom / IR feed paths.
_FEED_PATHS: tuple[str, ...] = (
    "/newsroom/rss",
    "/investor-relations/news",
    "/investors/news",
    "/news/releases",
    "/press-releases/rss",
    "/rss",
    "/feed",
)


@dataclass
class PressReleaseResult:
    ticker: str
    items: list[NewsItem] = field(default_factory=list)
    provider: str = "company_press_release"
    feed_url: str | None = None
    retrieved_at: str = ""
    warnings: list[str] = field(default_factory=list)
    # Phase 24.1.1 — precise feed status + item accounting.
    status: str = PressReleaseStatus.not_discovered.value
    items_seen: int = 0  # total items parsed from the feed (any date)
    items_used: int = 0  # items within the lookback window (returned in `items`)

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()


def _parse_feed_date(published_at: str | None) -> date | None:
    """Parse an RSS (RFC822) or ISO-8601 date string into a date. None on failure."""
    if not published_at:
        return None
    raw = published_at.strip()
    # RFC822 (RSS pubDate), e.g. "Wed, 15 Jul 2026 14:59:11 GMT".
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.date()
    except (TypeError, ValueError, IndexError):
        pass
    # ISO-8601 (Atom updated/published), e.g. "2026-07-15T14:59:11.792Z".
    iso = raw.replace("Z", "+00:00")
    for candidate in (iso, iso[:19], raw[:10]):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                continue
    return None


def _within_lookback(
    published_at: str | None, lookback_days: int, today: date | None = None
) -> bool:
    """
    True if an item is within the lookback window.

    Items with no parseable date are KEPT (returned True) so a feed that omits
    dates is not silently discarded — better to surface than to hide.
    """
    d = _parse_feed_date(published_at)
    if d is None:
        return True
    ref = today or datetime.now(timezone.utc).date()
    age = (ref - d).days
    return -1 <= age <= lookback_days


def discover_feed_urls(website: str | None) -> list[str]:
    """Build candidate feed URLs from a company website. No network."""
    if not website:
        return []
    site = website.strip()
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    parsed = urlparse(site)
    if not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [urljoin(base, path) for path in _FEED_PATHS]


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


# ---------------------------------------------------------------------------
# Phase 24.1.2 — canonical article link extraction (reject media/image URLs)
# ---------------------------------------------------------------------------

# Image/video file extensions that must never be used as an evidence URL.
_MEDIA_EXTS: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif",
    ".mp4", ".mov", ".webm",
)

# Path fragments that indicate a media/asset URL, not an article page.
_MEDIA_PATH_MARKERS: tuple[str, ...] = (
    "/images/", "/image/", "/media/", "/thumbnail/", "/thumbnails/", "/tile/",
)


def _is_http(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def is_media_url(url: str | None) -> bool:
    """True if a URL points at an image/media asset rather than an article page."""
    if not url:
        return False
    low = url.strip().lower()
    path = low.split("#", 1)[0].split("?", 1)[0]
    if path.endswith(_MEDIA_EXTS):
        return True
    # Apple newsroom tiles look like "...hero-lp.jpg.og.jpg".
    if ".og.jpg" in low or ".og.png" in low:
        return True
    if any(marker in low for marker in _MEDIA_PATH_MARKERS):
        return True
    return False


@dataclass
class CanonicalFeedLinkResult:
    canonical_url: str | None = None
    media_url: str | None = None
    quality: str = "missing"  # canonical_article | rejected_media_only | missing


def extract_canonical_feed_link(
    *,
    rss_link: str | None,
    atom_links: list[tuple[str, str, str]],  # (rel, type, href)
    guid: str | None,
    orig_link: str | None,
    media_urls: list[str],
    feed_base: str | None = None,
) -> CanonicalFeedLinkResult:
    """
    Choose the canonical article URL for a feed entry, never a media/image URL.

    Priority: RSS <link> text → Atom alternate/html <link> → any non-media Atom
    <link> → <guid>/<id> article URL → feedburner:origLink → (relative resolved
    against the feed base). Image/media URLs are captured in ``media_url`` and
    are NEVER returned as the canonical evidence link.
    """

    def _resolve(u: str | None) -> str | None:
        if not u:
            return None
        u = u.strip()
        if not _is_http(u) and feed_base:
            return urljoin(feed_base, u)
        return u

    def _first_media() -> str | None:
        for m in media_urls:
            if m:
                return m.strip()
        return None

    candidates: list[str | None] = []
    # 1. RSS <link> text.
    candidates.append(_resolve(rss_link))
    # 2. Atom alternate / html-typed <link>.
    for require_html in (True, False):
        for rel, typ, href in atom_links:
            rel_l = (rel or "").lower()
            if rel_l and rel_l != "alternate":
                continue
            if require_html and (typ or "").lower() not in (
                "text/html", "application/xhtml+xml", ""
            ):
                continue
            candidates.append(_resolve(href))
    # 3. guid / id, 4. feedburner origLink.
    candidates.append(_resolve(guid))
    candidates.append(_resolve(orig_link))

    for cand in candidates:
        if cand and _is_http(cand) and not is_media_url(cand):
            return CanonicalFeedLinkResult(
                canonical_url=cand,
                media_url=_first_media(),
                quality="canonical_article",
            )

    media = _first_media()
    if media:
        return CanonicalFeedLinkResult(
            canonical_url=None, media_url=media, quality="rejected_media_only"
        )
    return CanonicalFeedLinkResult(canonical_url=None, media_url=None, quality="missing")


def parse_feed(
    xml_text: str,
    source_name: str | None,
    feed_url: str | None = None,
    provider_name: str = "company_press_release",
    max_items: int = 20,
) -> list[NewsItem]:
    """
    Parse an RSS or Atom feed into T1 (company-owned) NewsItems.

    Pure function — no network. Handles both RSS (<item>) and Atom (<entry>).
    Returns [] on any parse error.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: list[NewsItem] = []

    # RSS: rss/channel/item ; Atom: feed/entry
    entries: list[ET.Element] = []
    for el in root.iter():
        tag = _strip_ns(el.tag).lower()
        if tag in ("item", "entry"):
            entries.append(el)

    for entry in entries[:max_items]:
        title: str | None = None
        published: str | None = None
        summary: str | None = None
        rss_link: str | None = None            # RSS <link> text
        atom_links: list[tuple[str, str, str]] = []  # (rel, type, href)
        guid: str | None = None
        orig_link: str | None = None           # feedburner:origLink / originalLink
        media_urls: list[str] = []

        for child in entry:
            ctag = _strip_ns(child.tag).lower()
            href = child.attrib.get("href")
            if ctag == "title" and title is None:
                title = _text(child)
            elif ctag == "link":
                if href is not None:
                    # Atom <link rel=... type=... href=...> (may repeat).
                    atom_links.append(
                        (child.attrib.get("rel", "") or "",
                         child.attrib.get("type", "") or "", href)
                    )
                    rel = (child.attrib.get("rel", "") or "").lower()
                    typ = (child.attrib.get("type", "") or "").lower()
                    if rel == "enclosure" or typ.startswith(("image/", "video/")) \
                            or is_media_url(href):
                        media_urls.append(href)
                elif rss_link is None:
                    rss_link = _text(child)  # RSS <link>text</link>
            elif ctag in ("guid", "id") and guid is None:
                guid = _text(child)
            elif ctag in ("origlink", "originallink") and orig_link is None:
                orig_link = _text(child)
            elif ctag == "enclosure":
                url = child.attrib.get("url")
                etype = (child.attrib.get("type", "") or "").lower()
                if url and (etype.startswith(("image/", "video/")) or is_media_url(url)):
                    media_urls.append(url)
            elif ctag in ("content", "thumbnail") and child.attrib.get("url"):
                # media:content / media:thumbnail (namespaced) carry a url attr.
                media_urls.append(child.attrib["url"])
            elif ctag in ("pubdate", "published", "updated", "date") and published is None:
                published = _text(child)
            elif ctag in ("description", "summary", "content") and summary is None:
                summary = _text(child)

        if not title:
            continue

        if guid and is_media_url(guid):
            media_urls.append(guid)

        link_result = extract_canonical_feed_link(
            rss_link=rss_link,
            atom_links=atom_links,
            guid=guid,
            orig_link=orig_link,
            media_urls=media_urls,
            feed_base=feed_url,
        )

        items.append(
            NewsItem(
                headline=title,
                url=link_result.canonical_url,
                published_at=published,
                source_name=source_name,
                summary=summary,
                provider_name=provider_name,
                source_tier=SourceTier.T1_primary_filing.value,
                media_url=link_result.media_url,
            )
        )

    return items


class CompanyPressReleaseProvider:
    """Discovers and parses a company's own press-release / IR feed (T1)."""

    provider_name = "company_press_release"

    async def _fetch(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=6.0,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                ctype = resp.headers.get("content-type", "").lower()
                feed_like_ctype = any(k in ctype for k in ("xml", "rss", "atom"))
                head = resp.text[:200].lstrip()
                feed_like_body = (
                    head.startswith("<?xml") or "<rss" in head or "<feed" in head
                )
                if not feed_like_ctype and not feed_like_body:
                    # Only accept feed-like responses; do not scrape HTML pages.
                    return None
                return resp.text
        except Exception:
            return None

    async def get_press_releases(
        self,
        ticker: str,
        company_name: str | None = None,
        website: str | None = None,
        lookback_days: int = 90,
        max_items: int = 20,
        feed_urls: list[str] | None = None,
    ) -> PressReleaseResult:
        """
        Attempt to discover and parse the company's own press-release feed.

        ``feed_urls`` (Phase 24.1) are explicit candidate feeds from company
        source discovery (e.g. a curated newsroom RSS). They are tried FIRST,
        before the website-derived common paths.

        Phase 24.1.1: returns a precise ``status`` so the report can distinguish
        "no feed known" from "feed discovered but unreadable" from "feed readable
        but no recent items" from "feed contributed events". Never raises.
        """
        explicit = [u for u in (feed_urls or []) if u]
        derived = [u for u in discover_feed_urls(website) if u not in explicit]
        candidates = explicit + derived
        had_explicit = bool(explicit)

        if not candidates:
            return PressReleaseResult(
                ticker=ticker.upper(),
                status=PressReleaseStatus.not_discovered.value,
                warnings=[
                    "Company primary news source unavailable: no company website / "
                    "IR feed URL is known for this issuer. Press-release catalysts "
                    "were not collected (SEC filings and any news provider still "
                    "apply)."
                ],
            )

        source_name = f"{company_name or ticker} newsroom"
        readable_but_stale_url: str | None = None
        stale_items_seen = 0

        for url in candidates:
            xml_text = await self._fetch(url)
            if not xml_text:
                continue
            all_items = parse_feed(
                xml_text,
                source_name=source_name,
                feed_url=url,
                provider_name=self.provider_name,
                max_items=max_items,
            )
            if not all_items:
                # Fetched but unparseable → try the next candidate.
                continue
            recent = [
                it for it in all_items if _within_lookback(it.published_at, lookback_days)
            ]
            if recent:
                return PressReleaseResult(
                    ticker=ticker.upper(),
                    items=recent,
                    feed_url=url,
                    status=PressReleaseStatus.feed_discovered_with_items.value,
                    items_seen=len(all_items),
                    items_used=len(recent),
                )
            # Readable feed but all items are older than the lookback window.
            if readable_but_stale_url is None:
                readable_but_stale_url = url
                stale_items_seen = len(all_items)

        # No candidate yielded recent items. Classify precisely.
        if readable_but_stale_url is not None:
            return PressReleaseResult(
                ticker=ticker.upper(),
                feed_url=readable_but_stale_url,
                status=PressReleaseStatus.feed_discovered_no_recent_items.value,
                items_seen=stale_items_seen,
                items_used=0,
                warnings=[
                    "Company press-release feed was discovered at "
                    f"{readable_but_stale_url}, but no items fell within the last "
                    f"{lookback_days}-day lookback window. Press-release catalysts "
                    "were not collected (SEC filings and any news provider still "
                    "apply)."
                ],
            )

        if had_explicit:
            # A discovered feed URL existed but could not be read/parsed.
            attempted = explicit[0]
            return PressReleaseResult(
                ticker=ticker.upper(),
                feed_url=attempted,
                status=PressReleaseStatus.feed_discovered_unreadable.value,
                warnings=[
                    "Company press-release feed was discovered at "
                    f"{attempted}, but it could not be read or parsed (fetch "
                    "failed, non-feed response, or malformed feed). Press-release "
                    "catalysts were not collected (SEC filings and any news "
                    "provider still apply)."
                ],
            )

        # Only website-derived common paths were tried and none were readable.
        probed = website or "the company website"
        return PressReleaseResult(
            ticker=ticker.upper(),
            status=PressReleaseStatus.not_discovered.value,
            warnings=[
                "Company primary news source unavailable: no readable RSS/Atom feed "
                f"was found at common paths for {probed}. Press-release catalysts "
                "were not collected."
            ],
        )
