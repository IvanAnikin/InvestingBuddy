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
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import NewsItem

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

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()


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
        link: str | None = None
        published: str | None = None
        summary: str | None = None

        for child in entry:
            ctag = _strip_ns(child.tag).lower()
            if ctag == "title" and title is None:
                title = _text(child)
            elif ctag == "link":
                # RSS: text; Atom: href attribute
                link = _text(child) or child.attrib.get("href") or link
            elif ctag in ("pubdate", "published", "updated", "date") and published is None:
                published = _text(child)
            elif ctag in ("description", "summary", "content") and summary is None:
                summary = _text(child)

        if not title:
            continue

        items.append(
            NewsItem(
                headline=title,
                url=link,
                published_at=published,
                source_name=source_name,
                summary=summary,
                provider_name=provider_name,
                source_tier=SourceTier.T1_primary_filing.value,
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
        source discovery (e.g. a curated newsroom RSS). They are tried first,
        before the website-derived common paths.

        Never raises. When no website / feed is known or no feed is found,
        returns an explicit warning and no items.
        """
        # Discovery-provided feeds first, then website-derived common paths.
        candidates: list[str] = []
        for u in (feed_urls or []):
            if u and u not in candidates:
                candidates.append(u)
        for u in discover_feed_urls(website):
            if u not in candidates:
                candidates.append(u)
        if not candidates:
            return PressReleaseResult(
                ticker=ticker.upper(),
                warnings=[
                    "Company primary news source unavailable: no company website / "
                    "IR feed URL is known for this issuer. Press-release catalysts "
                    "were not collected (SEC filings and any news provider still "
                    "apply)."
                ],
            )

        source_name = f"{company_name or ticker} newsroom"
        for url in candidates:
            xml_text = await self._fetch(url)
            if not xml_text:
                continue
            items = parse_feed(
                xml_text,
                source_name=source_name,
                feed_url=url,
                provider_name=self.provider_name,
                max_items=max_items,
            )
            if items:
                return PressReleaseResult(
                    ticker=ticker.upper(),
                    items=items,
                    feed_url=url,
                )

        probed = website or "the discovered company feeds"
        return PressReleaseResult(
            ticker=ticker.upper(),
            warnings=[
                "Company primary news source unavailable: no readable RSS/Atom feed "
                f"found at common paths for {probed}. Press-release catalysts were "
                "not collected."
            ],
        )
