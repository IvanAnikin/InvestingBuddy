"""
Phase 24.1.2 — Press-release canonical link fix.

Company press-release `source_url` must be the canonical article/press-release
page, never an image/media URL (e.g. Apple's `…tile/…jpg.og.jpg` enclosures).
Media URLs are captured separately as `media_url` and are never used as evidence.

Root cause covered: Apple's newsroom feed is Atom with two `<link>` elements per
entry — the article (`rel` empty) and an image enclosure (`rel="enclosure"
type="image/jpeg"`). The old parser let the last `<link>` win, so the image URL
became `source_url`.

No live external call — every feed is an inline fixture.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from unittest.mock import AsyncMock, patch

from app.agents.research_team.catalyst_agent import run_catalyst_agent
from app.integrations.providers.company_press_release_provider import (
    CompanyPressReleaseProvider,
    PressReleaseStatus,
    extract_canonical_feed_link,
    is_media_url,
    parse_feed,
)
from app.integrations.providers.sec_recent_filings_provider import RecentFilingsResult
from app.schemas.catalyst import CatalystEvent
from app.services.catalyst_discovery_service import discover_catalysts
from app.services.final_report_generator import (
    _build_news_catalyst_discovery,
    run_safety_gate,
)

TODAY = datetime.date.today()
RECENT = (TODAY - datetime.timedelta(days=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
RECENT_ISO = (TODAY - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

ARTICLE = "https://www.apple.com/newsroom/2026/07/major-league-soccer-returns-to-apple-tv-tomorrow/"
IMAGE = "https://www.apple.com/newsroom/images/2026/07/major-league-soccer-returns-to-apple-tv-tomorrow/tile/Apple-2026-MLS-Season-Restart-hero-lp.jpg.og.jpg"

_FORBIDDEN = re.compile(
    r"(?i)\b(BUY|SELL|HOLD|WATCH)\b|price target|fair value|upside|downside"
)


def _run(coro):
    return asyncio.run(coro)


# Apple-like Atom feed: article <link> (no rel) + image enclosure <link>.
APPLE_ATOM = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Apple Newsroom</title>
  <entry>
    <title>Major League Soccer returns to Apple TV tomorrow</title>
    <link href="{ARTICLE}"/>
    <id>{ARTICLE}</id>
    <updated>{RECENT_ISO}</updated>
    <content type="html">Following a break, MLS returns.</content>
    <link rel="enclosure" type="image/jpeg" href="{IMAGE}"/>
  </entry>
</feed>"""

# RSS variant: article <link> text + image <guid> + media:content image.
APPLE_RSS = f"""<?xml version="1.0"?>
<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
  <item>
    <title>Apple announces record Emmy nominations</title>
    <link>{ARTICLE}</link>
    <guid>{IMAGE}</guid>
    <pubDate>{RECENT}</pubDate>
    <media:content url="{IMAGE}" type="image/jpeg"/>
  </item>
</channel></rss>"""


class _FakeSec:
    def __init__(self, events):
        self._events = events

    async def get_recent_events(self, ticker, cik=None, company_name=None,
                                lookback_days=90, max_events=20):
        return RecentFilingsResult(ticker=ticker, cik="320193", events=self._events)


def _sec_event():
    return CatalystEvent(
        id="sec1", ticker="AAPL", headline="SEC 8-K", source_tier="T2_regulator_or_gov",
        normalized_event_type="sec_filing", form_type="8-K",
        filing_date=TODAY.isoformat(), event_date=TODAY.isoformat(),
    )


# ===========================================================================
# 1–12  Canonical link extraction
# ===========================================================================


class TestCanonicalLinks:
    def test_1_atom_article_link_over_image_enclosure(self):
        items = parse_feed(APPLE_ATOM, "Apple newsroom", feed_url="https://www.apple.com/newsroom/rss-feed.rss")
        assert items[0].url == ARTICLE

    def test_2_media_url_stored_separately(self):
        items = parse_feed(APPLE_ATOM, "Apple newsroom")
        assert items[0].media_url == IMAGE

    def test_3_source_url_never_image_extension(self):
        for feed in (APPLE_ATOM, APPLE_RSS):
            items = parse_feed(feed, "Apple newsroom")
            for it in items:
                assert it.url is None or not it.url.lower().split("?")[0].endswith(
                    (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif")
                )

    def test_4_source_url_never_media_path(self):
        items = parse_feed(APPLE_ATOM, "Apple newsroom")
        assert items[0].url and "/images/" not in items[0].url
        assert "/tile/" not in items[0].url

    def test_5_atom_alternate_link_selected(self):
        atom = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>t</title>
        <link rel="enclosure" type="image/jpeg" href="{IMAGE}"/>
        <link rel="alternate" type="text/html" href="{ARTICLE}"/>
        <updated>{RECENT_ISO}</updated></entry></feed>"""
        items = parse_feed(atom, "x")
        assert items[0].url == ARTICLE

    def test_6_rss_guid_article_used_when_link_missing(self):
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>t</title><guid>{ARTICLE}</guid><pubDate>{RECENT}</pubDate>
        </item></channel></rss>"""
        items = parse_feed(rss, "x")
        assert items[0].url == ARTICLE

    def test_7_rss_guid_image_rejected(self):
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>t</title><guid>{IMAGE}</guid><pubDate>{RECENT}</pubDate>
        </item></channel></rss>"""
        items = parse_feed(rss, "x")
        assert items[0].url is None
        assert items[0].media_url == IMAGE

    def test_8_relative_link_resolved_against_base(self):
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>t</title><link>/newsroom/2026/07/story/</link>
        <pubDate>{RECENT}</pubDate></item></channel></rss>"""
        items = parse_feed(rss, "x", feed_url="https://www.apple.com/newsroom/rss-feed.rss")
        assert items[0].url == "https://www.apple.com/newsroom/2026/07/story/"

    def test_9_enclosure_image_rejected_as_source(self):
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>t</title><enclosure url="{IMAGE}" type="image/jpeg"/>
        <pubDate>{RECENT}</pubDate></item></channel></rss>"""
        items = parse_feed(rss, "x")
        assert items[0].url is None
        assert items[0].media_url == IMAGE

    def test_10_description_image_not_used_as_source(self):
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>t</title><description>&lt;img src="{IMAGE}"/&gt;</description>
        <link>{ARTICLE}</link><pubDate>{RECENT}</pubDate></item></channel></rss>"""
        items = parse_feed(rss, "x")
        assert items[0].url == ARTICLE  # article link, not the img in description

    def test_11_only_image_yields_no_source_url(self):
        result = extract_canonical_feed_link(
            rss_link=None, atom_links=[("enclosure", "image/jpeg", IMAGE)],
            guid=IMAGE, orig_link=None, media_urls=[IMAGE], feed_base=None,
        )
        assert result.canonical_url is None
        assert result.media_url == IMAGE
        assert result.quality == "rejected_media_only"

    def test_12_is_media_url_case_insensitive(self):
        assert is_media_url("https://x.example/A/TILE/PHOTO.JPG")
        assert is_media_url(IMAGE)
        assert not is_media_url(ARTICLE)


# ===========================================================================
# 13–16  Provider + discovery integration
# ===========================================================================


class TestProviderIntegration:
    def _discover_with_feed(self, feed_xml):
        prov = CompanyPressReleaseProvider()
        with patch.object(prov, "_fetch", new=AsyncMock(return_value=feed_xml)):
            return _run(
                discover_catalysts(
                    ticker="AAPL", company_name="Apple Inc.", exchange="NASDAQ",
                    country="US", sec_provider=_FakeSec([_sec_event()]),
                    press_release_provider=prov, news_provider=None,
                    include_news=False, include_source_discovery=True,
                )
            )

    def test_13_aapl_press_event_has_canonical_url(self):
        res = self._discover_with_feed(APPLE_ATOM)
        assert res.press_release_events
        ev = res.press_release_events[0]
        assert ev.source_url == ARTICLE
        assert ev.source_tier == "T1_primary_filing"
        assert ev.source_url_quality == "canonical_article"
        assert ev.media_url == IMAGE

    def test_14_report_link_uses_canonical_url(self):
        res = self._discover_with_feed(APPLE_ATOM)
        md = run_catalyst_agent(res).markdown
        assert ARTICLE in md
        assert ".jpg.og.jpg" not in md

    def test_15_payload_source_url_canonical(self):
        res = self._discover_with_feed(APPLE_ATOM)
        section = _build_news_catalyst_discovery(res.to_report_dict())
        for row in section["recent_events"]["value"]:
            url = row.get("source_url")
            assert not (url and is_media_url(url))
        # At least one row carries the canonical article + media separated.
        press_rows = [
            r for r in section["recent_events"]["value"]
            if r.get("source_url") == ARTICLE
        ]
        assert press_rows and press_rows[0]["media_url"] == IMAGE

    def test_16_feed_status_with_items_preserved(self):
        res = self._discover_with_feed(APPLE_ATOM)
        assert res.company_press_release_status == (
            PressReleaseStatus.feed_discovered_with_items.value
        )


# ===========================================================================
# 17–21  Safety / backward compat
# ===========================================================================


class TestSafety:
    def _res(self):
        prov = CompanyPressReleaseProvider()
        with patch.object(prov, "_fetch", new=AsyncMock(return_value=APPLE_ATOM)):
            return _run(
                discover_catalysts(
                    ticker="AAPL", company_name="Apple Inc.", exchange="NASDAQ",
                    country="US", sec_provider=_FakeSec([_sec_event()]),
                    press_release_provider=prov, include_news=False,
                    include_source_discovery=True,
                )
            )

    def test_17_no_forbidden_output(self):
        res = self._res()
        blob = run_catalyst_agent(res).markdown + json.dumps(res.to_report_dict())
        assert _FORBIDDEN.search(blob) is None

    def test_18_human_review_required(self):
        assert self._res().human_review_required is True

    def test_19_safety_gate_passes(self):
        section = _build_news_catalyst_discovery(self._res().to_report_dict())
        assert run_safety_gate({"news_catalyst_discovery": section}).passed is True

    def test_20_media_url_optional_missing_ok(self):
        # A plain RSS item with an article link and no media still works.
        rss = f"""<?xml version="1.0"?><rss><channel><item>
        <title>Apple update</title><link>{ARTICLE}</link>
        <pubDate>{RECENT}</pubDate></item></channel></rss>"""
        items = parse_feed(rss, "x")
        assert items[0].url == ARTICLE and items[0].media_url is None

    def test_21_dedup_by_canonical_url(self):
        # Two entries, same article, different image → one press event.
        atom = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>a</title><link href="{ARTICLE}"/><updated>{RECENT_ISO}</updated>
        <link rel="enclosure" type="image/jpeg" href="{IMAGE}"/></entry>
        <entry><title>a</title><link href="{ARTICLE}"/><updated>{RECENT_ISO}</updated>
        <link rel="enclosure" type="image/jpeg" href="{IMAGE.replace('MLS','OTHER')}"/></entry>
        </feed>"""
        prov = CompanyPressReleaseProvider()
        with patch.object(prov, "_fetch", new=AsyncMock(return_value=atom)):
            res = _run(
                discover_catalysts(
                    ticker="AAPL", company_name="Apple Inc.", exchange="NASDAQ",
                    country="US", sec_provider=_FakeSec([]),
                    press_release_provider=prov, include_news=False,
                    include_source_discovery=True,
                )
            )
        assert len(res.press_release_events) == 1
