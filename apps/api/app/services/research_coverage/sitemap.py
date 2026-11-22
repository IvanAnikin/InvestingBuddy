"""Edison Group sitemap → ``research_coverage_pages`` — V3.21 slice 21.7 (ADR-114).

THE REFRESH (weekly, or ``scripts/refresh_research_coverage.py``)
=================================================================
1. ``robots.txt`` of ``https://www.edisongroup.com`` is read through
   ``safe_fetch_document`` (host pinned to the Edison hosts, resolved-IP checked, 500 KiB
   cap) and decided with the platform's RFC 9309 parser for ``/sitemap.xml``. Disallowed
   or unreachable (429, 5xx, a network failure) → nothing is fetched (fail closed).
2. ``sitemap.xml`` — one GET, same host pin, :data:`MAX_SITEMAP_BYTES` cap. A body cut at
   the cap is REFUSED rather than half-applied.
3. The XML is parsed only after a ``<!DOCTYPE`` / ``<!ENTITY`` check (no entity can
   expand), bounded at :data:`MAX_URLS` entries. Only ``https`` URLs on the Edison hosts
   are read; ``/equity/<slug>/[<id>/]`` is a company page, ``/research/<slug>/<id>/`` a
   note; everything else (videos, podcasts, sector pages) is skipped.
4. **Drift fails closed:** a file with no company page at all is a layout the parser was
   not written against, and the table is left untouched.
5. Upsert by URL: a new URL is inserted with ``first_seen_at``; a known one gets its
   ``lastmod`` and ``last_seen_at``. No row is ever deleted. One ``now`` stamp per
   refresh, so "listed in the latest sitemap" is ``last_seen_at`` = the newest stamp.

Only URLs, slugs, a slug-derived title and dates are stored — no page is fetched here.
Logs carry counts and codes, never a URL list or a body.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import sqlalchemy as sa

from app.core.structured_logging import log_event
from app.models.research_coverage import ResearchCoveragePage
from app.services.research_coverage.names import slug_key, title_from_slug
from app.services.research_coverage.providers import EDISON

logger = logging.getLogger(__name__)

EDISON_ORIGIN = "https://www.edisongroup.com"
EDISON_SITEMAP_URL = EDISON_ORIGIN + "/sitemap.xml"
EDISON_ROBOTS_URL = EDISON_ORIGIN + "/robots.txt"
#: The live file was 1.09 MB on 2026-11-22; eight times that before we refuse it.
MAX_SITEMAP_BYTES = 8 * 1024 * 1024
MAX_ROBOTS_BYTES = 500 * 1024
MAX_URLS = 50_000
FETCH_DEADLINE_SECONDS = 60.0
XML_CONTENT_TYPES: tuple[str, ...] = ("text/xml", "application/xml")

STATE_OK = "ok"
STATE_ROBOTS_DISALLOWED = "robots_disallowed"
STATE_ROBOTS_UNAVAILABLE = "robots_unavailable"
STATE_FETCH_FAILED = "fetch_failed"
STATE_TRUNCATED = "truncated"
STATE_PARSE_FAILED = "parse_failed"
STATE_LAYOUT_DRIFT = "layout_drift"

_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_EQUITY_RE = re.compile(r"^/equity/([a-z0-9][a-z0-9-]{0,150})/(?:(BMC-\d{1,8})/)?$")
_NOTE_RE = re.compile(r"^/research/([a-z0-9][a-z0-9-]{0,250})/([A-Za-z0-9-]{1,20})/$")


# ── parsing (pure) ───────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class CoverageEntry:
    provider: str
    url: str
    page_kind: str  # equity | note
    company_slug: str | None
    name_key: str | None
    title: str | None
    lastmod: datetime | None


@dataclass
class SitemapParse:
    entries: list[CoverageEntry] = field(default_factory=list)
    urls_total: int = 0
    skipped_off_host: int = 0
    skipped_other_paths: int = 0
    truncated: bool = False
    error: str | None = None

    @property
    def equity_pages(self) -> int:
        return sum(1 for e in self.entries if e.page_kind == "equity")

    @property
    def notes(self) -> int:
        return sum(1 for e in self.entries if e.page_kind == "note")


def parse_lastmod(value: str | None) -> datetime | None:
    """A W3C datetime (``2026-11-19T16:43:21+00:00`` or ``2026-11-19``) in UTC."""
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_edison_url(url: str) -> tuple[str, str] | None:
    """``("equity" | "note", slug)`` for an Edison page URL, else ``None``.

    Only ``https`` on the Edison hosts, no query, no fragment, no userinfo or port.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if (parts.scheme != "https" or (parts.hostname or "") not in EDISON.hosts
            or parts.port is not None or parts.username or parts.query or parts.fragment):
        return None
    m = _EQUITY_RE.match(parts.path)
    if m:
        return "equity", m.group(1)
    m = _NOTE_RE.match(parts.path)
    if m:
        return "note", m.group(1)
    return None


def _on_edison(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "") in EDISON.hosts


def parse_sitemap(raw: bytes | str, *, max_urls: int = MAX_URLS) -> SitemapParse:
    """The Edison sitemap's company pages and notes. Never raises."""
    out = SitemapParse()
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        out.error = "doctype_refused"
        return out
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        out.error = "malformed_xml"
        return out
    if root.tag not in (f"{_SM_NS}urlset", "urlset"):
        # A sitemap index (or anything else) is a layout this parser was not written for.
        out.error = "not_a_urlset"
        return out
    seen: set[str] = set()
    for node in root:
        if node.tag not in (f"{_SM_NS}url", "url"):
            continue
        if out.urls_total >= max_urls:
            out.truncated = True
            break
        out.urls_total += 1
        loc_el = node.find(f"{_SM_NS}loc")
        if loc_el is None:
            loc_el = node.find("loc")
        loc = (loc_el.text or "").strip() if loc_el is not None else ""
        if not loc or len(loc) > 2048:
            continue
        if not _on_edison(loc):
            out.skipped_off_host += 1
            continue
        kind_slug = classify_edison_url(loc)
        if kind_slug is None:
            out.skipped_other_paths += 1
            continue
        if loc in seen:
            continue
        seen.add(loc)
        kind, slug = kind_slug
        mod_el = node.find(f"{_SM_NS}lastmod")
        if mod_el is None:
            mod_el = node.find("lastmod")
        out.entries.append(CoverageEntry(
            provider=EDISON.key, url=loc, page_kind=kind, company_slug=slug,
            # A note's slug is its headline, not a company: it gets no name key.
            name_key=slug_key(slug) if kind == "equity" else None,
            title=title_from_slug(slug),
            lastmod=parse_lastmod(mod_el.text if mod_el is not None else None)))
    return out


# ── fetching (host pinned, robots first) ──────────────────────────────────── #


@dataclass(frozen=True)
class RawAnswer:
    """One bounded GET. ``status`` None = never answered (blocked, timeout, network)."""

    status: int | None
    body: bytes = b""
    failed: bool = False
    truncated: bool = False


#: ``(url, max_bytes) -> RawAnswer`` — the network seam; tests never reach the network.
Fetcher = Callable[[str, int], Awaitable[RawAnswer]]


async def default_fetcher(url: str, max_bytes: int) -> RawAnswer:
    """``safe_fetch_document`` pinned to the Edison hosts. Never raises."""
    from app.services.sources.document_fetcher import safe_fetch_document

    try:
        result = await safe_fetch_document(
            url, allowed_domains=EDISON.hosts, resolve_ip=True,
            extra_text_content_types=XML_CONTENT_TYPES, max_bytes=max_bytes,
            total_deadline_seconds=FETCH_DEADLINE_SECONDS)
    except Exception:  # noqa: BLE001 - the fetcher never raises by contract
        return RawAnswer(None, failed=True)
    if result.blocked or (result.error and result.status_code is None):
        return RawAnswer(None, failed=True)
    if result.error:
        return RawAnswer(result.status_code, failed=False)
    return RawAnswer(result.status_code, result.content or b"", truncated=result.truncated)


async def robots_decision(fetch: Fetcher, path: str = "/sitemap.xml") -> str:
    """``allowed`` | ``no_robots`` | ``disallowed`` | ``unavailable`` for ``path``."""
    from app.services.web_research import robots

    answer = await fetch(EDISON_ROBOTS_URL, MAX_ROBOTS_BYTES)
    small = robots.SmallFetch(answer.status, answer.body,
                              failed=answer.failed or answer.status is None)
    return robots.policy_from_answer(small).decide(path)


# ── storing ──────────────────────────────────────────────────────────────── #


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0


async def upsert_pages(session: Any, entries: Iterable[CoverageEntry], *, now: datetime,
                       discovered_via: str) -> UpsertResult:
    """Insert new URLs, refresh known ones. Idempotent. Portable (no dialect upsert)."""
    result = UpsertResult()
    batch = {e.url: e for e in entries}
    urls = list(batch)
    known: dict[str, uuid.UUID] = {}
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        rows = await session.execute(
            sa.select(ResearchCoveragePage.url, ResearchCoveragePage.id)
            .where(ResearchCoveragePage.url.in_(chunk)))
        known.update({u: pid for u, pid in rows.all()})
    updates: list[dict[str, Any]] = []
    for url in urls:
        entry = batch[url]
        if url in known:
            updates.append({"id": known[url], "lastmod": entry.lastmod,
                            "last_seen_at": now, "title": entry.title,
                            "name_key": entry.name_key,
                            "company_slug": entry.company_slug})
            result.updated += 1
        else:
            session.add(ResearchCoveragePage(
                id=uuid.uuid4(), provider=entry.provider, url=url,
                page_kind=entry.page_kind, company_slug=entry.company_slug,
                name_key=entry.name_key, title=entry.title, lastmod=entry.lastmod,
                first_seen_at=now, last_seen_at=now, discovered_via=discovered_via))
            result.inserted += 1
    for i in range(0, len(updates), 500):
        # ORM bulk UPDATE by primary key: one executemany per chunk.
        await session.execute(sa.update(ResearchCoveragePage), updates[i:i + 500])
    await session.flush()
    return result


# ── the refresh ──────────────────────────────────────────────────────────── #


@dataclass
class RefreshResult:
    state: str
    robots: str | None = None
    urls_total: int = 0
    equity_pages: int = 0
    notes: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_off_host: int = 0
    dry_run: bool = False
    refreshed_at: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == STATE_OK

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "robots": self.robots, "urls_total": self.urls_total,
                "equity_pages": self.equity_pages, "notes": self.notes,
                "inserted": self.inserted, "updated": self.updated,
                "skipped_off_host": self.skipped_off_host, "dry_run": self.dry_run,
                "refreshed_at": self.refreshed_at, "error": self.error,
                "source_url": EDISON_SITEMAP_URL}


async def refresh_edison_index(
    session: Any, *, fetch: Fetcher | None = None, now: datetime | None = None,
    dry_run: bool = False,
) -> RefreshResult:
    """robots → sitemap → parse → upsert. Flushes; the caller commits. Never raises for a
    network or layout outcome (a database error propagates)."""
    from app.services.web_research import robots

    fetcher = fetch or default_fetcher
    stamp = now or datetime.now(timezone.utc)
    decision = await robots_decision(fetcher)
    if decision == robots.ROBOTS_DISALLOWED:
        return _logged(RefreshResult(STATE_ROBOTS_DISALLOWED, robots=decision,
                                     dry_run=dry_run))
    if decision == robots.ROBOTS_UNAVAILABLE:
        return _logged(RefreshResult(STATE_ROBOTS_UNAVAILABLE, robots=decision,
                                     dry_run=dry_run))
    answer = await fetcher(EDISON_SITEMAP_URL, MAX_SITEMAP_BYTES)
    if answer.failed or answer.status is None or not (200 <= answer.status < 300):
        return _logged(RefreshResult(
            STATE_FETCH_FAILED, robots=decision, dry_run=dry_run,
            error=f"http {answer.status}" if answer.status else "no answer"))
    if answer.truncated:
        return _logged(RefreshResult(STATE_TRUNCATED, robots=decision, dry_run=dry_run,
                                     error=f"larger than {MAX_SITEMAP_BYTES} bytes"))
    parsed = parse_sitemap(answer.body)
    result = RefreshResult(
        STATE_OK, robots=decision, urls_total=parsed.urls_total,
        equity_pages=parsed.equity_pages, notes=parsed.notes,
        skipped_off_host=parsed.skipped_off_host, dry_run=dry_run)
    if parsed.error:
        result.state, result.error = STATE_PARSE_FAILED, parsed.error
        return _logged(result)
    if parsed.equity_pages == 0:
        result.state, result.error = STATE_LAYOUT_DRIFT, "no /equity/ page in the sitemap"
        return _logged(result)
    if dry_run:
        return _logged(result)
    stored = await upsert_pages(session, parsed.entries, now=stamp,
                                discovered_via="sitemap")
    result.inserted, result.updated = stored.inserted, stored.updated
    result.refreshed_at = stamp.isoformat()
    return _logged(result)


def _logged(result: RefreshResult) -> RefreshResult:
    log_event(logger, "v3_research_coverage_refresh", provider=EDISON.key,
              state=result.state, urls_total=result.urls_total,
              equity_pages=result.equity_pages, notes=result.notes,
              inserted=result.inserted, updated=result.updated, dry_run=result.dry_run)
    return result


__all__ = [
    "EDISON_ROBOTS_URL",
    "EDISON_SITEMAP_URL",
    "MAX_SITEMAP_BYTES",
    "STATE_FETCH_FAILED",
    "STATE_LAYOUT_DRIFT",
    "STATE_OK",
    "STATE_PARSE_FAILED",
    "STATE_ROBOTS_DISALLOWED",
    "STATE_ROBOTS_UNAVAILABLE",
    "STATE_TRUNCATED",
    "CoverageEntry",
    "Fetcher",
    "RawAnswer",
    "RefreshResult",
    "SitemapParse",
    "UpsertResult",
    "classify_edison_url",
    "default_fetcher",
    "parse_lastmod",
    "parse_sitemap",
    "refresh_edison_index",
    "robots_decision",
    "upsert_pages",
]
