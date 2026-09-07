"""Bounded same-domain issuer traversal — V3.4 Slice 4.10.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT
=============================================
It is a **bounded walk of one issuer's own site**, on the platform's existing guarded
fetcher, to find the pages that link to the documents V3 wants. It is not a crawler, not
a general web spider, and not a browser: [ADR-051](../../../docs/DECISIONS.md) and the
2026-09-05 decisions rule out a paid crawler service, and governance §3 rules out browser
automation as anything but a last resort.

A JS-only IR page is therefore recorded as **partially inaccessible**, with the reason.
That is the honest outcome and it is a research gap somebody can act on. Slice 5A of
Phase 32A already measured the alternative: zero successful native extractions across
seven live issuers, with the success path existing only in unit tests.

EVERY WALK ENDS, AND SAYS WHICH BOUND ENDED IT
==============================================
`max_pages` · `max_depth` · `max_links_per_page` · `max_seconds` · the frontier. A walk
that stopped is not the same as a walk that finished, and a result reporting "12 pages"
without saying it hit its page cap invites a reader to conclude the site has 12 pages.
`stopped_by` is always populated: ``exhausted`` when the frontier emptied, a bound's name
otherwise.

SAME REGISTRABLE DOMAIN, AND THAT IS THE ALLOWLIST
==================================================
The traversal's allowlist is the issuer's own registrable domain plus whatever the caller
explicitly adds — which is how an off-domain CDN gets reached, since Pandora's documents
live on one. Everything else the guarded fetcher already refuses: non-HTTPS, internal
hosts, IP literals, a redirect that leaves the allowlist, and anything over the byte cap.

ROBOTS.TXT IS OBEYED
====================
Governance §10 requires it. The file is fetched through the same guarded fetcher, parsed
with the standard library, and a disallowed path is **skipped and counted** — not fetched
and discarded. When robots.txt cannot be retrieved the walk proceeds, because an
unreachable robots.txt is not a prohibition; when it is retrieved and disallows, that is
final.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.services.sources.safe_web_fetcher import (
    SafeLink,
    extract_links,
    host_of,
    is_safe_public_host,
    registrable_host_allowed,
    safe_fetch_page,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: Why a walk stopped. Closed, because "how far did we get" aggregated by reason is a
#: coverage finding and aggregated by free text is a list of sentences.
STOPPED_EXHAUSTED = "exhausted"
STOPPED_MAX_PAGES = "max_pages"
STOPPED_MAX_DEPTH = "max_depth"
STOPPED_MAX_SECONDS = "max_seconds"
STOPPED_DISABLED = "disabled"

STOP_REASONS: frozenset[str] = frozenset(
    {
        STOPPED_EXHAUSTED,
        STOPPED_MAX_PAGES,
        STOPPED_MAX_DEPTH,
        STOPPED_MAX_SECONDS,
        STOPPED_DISABLED,
    }
)

#: Why one page was not walked.
SKIP_ROBOTS = "robots_disallowed"
SKIP_OFF_DOMAIN = "off_domain"
SKIP_UNSAFE_HOST = "unsafe_host"
SKIP_ALREADY_SEEN = "already_seen"
SKIP_FETCH_FAILED = "fetch_failed"
SKIP_BLOCKED = "blocked"

#: The user agent robots.txt is evaluated against. The same string the fetcher sends, so
#: a site that names it in robots.txt gets the behaviour it asked for.
ROBOTS_AGENT = "InvestingBuddyResearchBot"

#: A page that returned a long body and almost no links is the JS-gated shape: the anchors
#: are rendered client-side and a non-browser fetch cannot see them. Recorded, never
#: worked around.
_JS_GATED_MIN_BODY = 20_000
_JS_GATED_MAX_LINKS = 2


@dataclass(frozen=True)
class TraversalBounds:
    """Every limit one walk runs under. All finite; none optional."""

    max_pages: int = 12
    max_depth: int = 2
    max_links_per_page: int = 40
    max_seconds: float = 45.0

    def __post_init__(self) -> None:
        for name in ("max_pages", "max_depth", "max_links_per_page", "max_seconds"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(
                    f"{name} must be positive: an unbounded walk of somebody else's "
                    "site is the thing this module exists to make impossible."
                )


@dataclass
class VisitedPage:
    """One page the walk actually fetched."""

    url: str
    depth: int
    status_code: int | None = None
    title: str | None = None
    link_count: int = 0
    document_link_count: int = 0
    #: True when the page looks JS-gated — a long body with almost no anchors. Recorded
    #: rather than escalated: a browser is not available and pretending otherwise would
    #: make an inaccessible page look like an empty one.
    appears_js_gated: bool = False


@dataclass
class TraversalResult:
    """What one walk found, and exactly how far it got.

    ``stopped_by`` is never empty. A walk that stopped is not a walk that finished, and a
    result reporting "12 pages" without saying it hit its page cap invites a reader to
    conclude the site has 12 pages.
    """

    start_url: str
    allowed_domains: tuple[str, ...] = ()
    stopped_by: str = STOPPED_EXHAUSTED
    pages: list[VisitedPage] = field(default_factory=list)
    #: Every document-looking link found, de-duplicated, in discovery order. These are
    #: **candidates**: this module never fetches a document, because turning a URL into
    #: bytes with a hash is the document fetcher's job and its guards are the ones that
    #: matter for a 30 MB PDF.
    document_links: list[SafeLink] = field(default_factory=list)
    #: Counts by reason. A skip nobody counted is a coverage claim nobody can check.
    skipped: dict[str, int] = field(default_factory=dict)
    frontier_remaining: int = 0
    elapsed_seconds: float = 0.0
    robots_consulted: bool = False
    notes: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def completed(self) -> bool:
        """True only when the frontier emptied. Reaching a bound is not completion."""
        return self.stopped_by == STOPPED_EXHAUSTED

    @property
    def partially_inaccessible(self) -> bool:
        """True when some part of the site could not be read without a browser."""
        return any(page.appears_js_gated for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_url": self.start_url,
            "allowed_domains": list(self.allowed_domains),
            "stopped_by": self.stopped_by,
            "completed": self.completed,
            "pages_visited": len(self.pages),
            "document_links": [
                {"url": link.url, "text": link.text} for link in self.document_links
            ],
            "skipped": dict(self.skipped),
            "frontier_remaining": self.frontier_remaining,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "robots_consulted": self.robots_consulted,
            "partially_inaccessible": self.partially_inaccessible,
            "notes": list(self.notes),
        }


def registrable_domain_of(url: str) -> str | None:
    """The issuer's own domain, used as the walk's allowlist.

    Naive last-two-labels, matching what ``registrable_host_allowed`` already does
    elsewhere in this codebase. It is deliberately *not* a public-suffix list: adding one
    is a dependency and a data file, and the failure direction here is safe — a
    co.uk-style host resolves to a broader domain than it should, which the guarded
    fetcher's other checks still bound, and the caller can always pass an explicit
    allowlist instead.
    """
    host = host_of(url)
    if not host or not is_safe_public_host(host):
        return None
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


async def _load_robots(
    start_url: str, *, allowed_domains: tuple[str, ...], cfg: Any, fetcher: Any
) -> RobotFileParser | None:
    """Fetch and parse robots.txt through the guarded fetcher.

    Returns ``None`` when it could not be retrieved, and the caller proceeds: an
    unreachable robots.txt is not a prohibition. A retrieved one that disallows **is**
    final.
    """
    parts = urlsplit(start_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        result = await fetcher(
            robots_url,
            allowed_domains=allowed_domains,
            keywords=(),
            cfg=cfg,
            resolve_ip=True,
        )
    except Exception:  # noqa: BLE001 - an unreachable robots.txt is not a prohibition
        return None
    body = getattr(result, "body_html", None)
    if not getattr(result, "ok", False) or not body:
        return None
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return parser


async def traverse_issuer_site(
    start_url: str,
    *,
    cfg: "Settings | None" = None,
    bounds: TraversalBounds | None = None,
    extra_domains: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    fetcher: Any = None,
    now: Any = None,
) -> TraversalResult:
    """Walk one issuer's site within its bounds. Never raises.

    ``keywords`` restricts which links are followed and which documents are collected;
    an empty tuple means "every same-domain link", which is what a first pass over an IR
    section wants.

    ``extra_domains`` is how an off-domain CDN is reached — Pandora's documents live on
    one — and it is **explicit** rather than inferred, because inferring it from a
    redirect would let any site widen its own allowlist.
    """
    from app.core.config import settings as default_settings

    cfg = cfg or default_settings
    bounds = bounds or TraversalBounds()
    clock = now or time.monotonic
    started = clock()
    result = TraversalResult(start_url=start_url)

    if not getattr(cfg, "v3_issuer_traversal_enabled", False):
        result.stopped_by = STOPPED_DISABLED
        result.notes.append(
            "V3_ISSUER_TRAVERSAL_ENABLED is off; no request was made."
        )
        return result

    domain = registrable_domain_of(start_url)
    if domain is None:
        result.stopped_by = STOPPED_EXHAUSTED
        result.skip(SKIP_UNSAFE_HOST)
        result.notes.append("the start URL is not a safe public HTTPS host")
        return result
    allowed = (domain, *(d.lower() for d in extra_domains if d))
    result.allowed_domains = allowed

    fetch = fetcher or safe_fetch_page
    robots = await _load_robots(
        start_url, allowed_domains=allowed, cfg=cfg, fetcher=fetch
    )
    result.robots_consulted = robots is not None

    frontier: deque[tuple[str, int]] = deque([(start_url, 0)])
    seen: set[str] = {start_url}
    documents: dict[str, SafeLink] = {}

    while frontier:
        if len(result.pages) >= bounds.max_pages:
            result.stopped_by = STOPPED_MAX_PAGES
            break
        if (clock() - started) >= bounds.max_seconds:
            result.stopped_by = STOPPED_MAX_SECONDS
            break
        url, depth = frontier.popleft()

        if robots is not None and not robots.can_fetch(ROBOTS_AGENT, url):
            # Skipped, never fetched-and-discarded. "We obeyed robots.txt" has to mean
            # the request did not happen.
            result.skip(SKIP_ROBOTS)
            continue
        try:
            page = await fetch(
                url,
                allowed_domains=allowed,
                keywords=keywords,
                cfg=cfg,
                resolve_ip=True,
            )
        except Exception:  # noqa: BLE001 - one bad page must not end the walk
            result.skip(SKIP_FETCH_FAILED)
            continue
        if getattr(page, "blocked", False):
            result.skip(SKIP_BLOCKED)
            continue
        if not getattr(page, "ok", False):
            result.skip(SKIP_FETCH_FAILED)
            continue

        body = getattr(page, "body_html", None) or ""
        links = extract_links(
            body,
            base_url=getattr(page, "final_url", None) or url,
            allowed_domains=allowed,
            keywords=keywords or ("",),
            max_links=bounds.max_links_per_page,
        )
        doc_links = [link for link in links if link.is_document]
        visited = VisitedPage(
            url=url,
            depth=depth,
            status_code=getattr(page, "status_code", None),
            title=getattr(page, "title", None),
            link_count=len(links),
            document_link_count=len(doc_links),
            appears_js_gated=(
                len(body) >= _JS_GATED_MIN_BODY and len(links) <= _JS_GATED_MAX_LINKS
            ),
        )
        result.pages.append(visited)
        for link in doc_links:
            documents.setdefault(link.url, link)

        if depth >= bounds.max_depth:
            # Not an error and not a skip of THIS page — it was visited. Its children
            # are simply out of range, which `frontier_remaining` will not show and
            # `stopped_by` should not claim.
            continue
        for link in links:
            if link.is_document or link.url in seen:
                if link.url in seen and not link.is_document:
                    result.skip(SKIP_ALREADY_SEEN)
                continue
            host = host_of(link.url)
            if not registrable_host_allowed(host, allowed):
                result.skip(SKIP_OFF_DOMAIN)
                continue
            seen.add(link.url)
            frontier.append((link.url, depth + 1))

    result.frontier_remaining = len(frontier)
    result.document_links = list(documents.values())
    result.elapsed_seconds = clock() - started
    if result.partially_inaccessible:
        result.notes.append(
            "At least one page returned a long body with almost no links, which is the "
            "shape of a JS-rendered IR page. Its documents are not reachable without a "
            "browser, and none is available: recorded as partially inaccessible rather "
            "than as an empty page."
        )
    return result


__all__ = [
    "ROBOTS_AGENT",
    "SKIP_ALREADY_SEEN",
    "SKIP_BLOCKED",
    "SKIP_FETCH_FAILED",
    "SKIP_OFF_DOMAIN",
    "SKIP_ROBOTS",
    "SKIP_UNSAFE_HOST",
    "STOPPED_DISABLED",
    "STOPPED_EXHAUSTED",
    "STOPPED_MAX_DEPTH",
    "STOPPED_MAX_PAGES",
    "STOPPED_MAX_SECONDS",
    "STOP_REASONS",
    "TraversalBounds",
    "TraversalResult",
    "VisitedPage",
    "registrable_domain_of",
    "traverse_issuer_site",
]
