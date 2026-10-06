"""Bounded crawling from seeds — open-web W5 (spec §11.1–11.2).

Crawling is **seeded only**: fetched search results and the verified issuer's own
investor-relations pages. Nothing here accepts a URL from anywhere else, and nothing here
fetches directly — every request goes through
:func:`~app.services.web_research.fetch.open_web_fetch` (robots.txt, TDMRep, the
denylist, the budget, the limiter and the W0 SSRF guard all live there), so a robots
``Disallow`` is honoured by construction.

HOW THIS RELATES TO ``traverse_issuer_site``
============================================
That function walks one issuer's site on the older allowlisted fetcher and keeps its own
robots handling. This module **composes** it rather than copying: it reuses its stop
vocabulary, its ``VisitedPage`` record, its public-suffix-safe ``registrable_domain_of``
and its JS-gated-page heuristic, and the HTML link extractor
(:func:`~app.services.sources.safe_web_fetcher.extract_links`, widened by one optional
``host_ok`` predicate for the cross-domain rule below). The walk itself differs in
transport (``open_web_fetch``, with provenance rows and the run's budget), scoring
(§11.2) and scope, so it is a separate loop and ``traverse_issuer_site`` is unchanged.

RULES (spec §11.1)
==================
==========================  ======================================================
Depth                       ≤ 2 from a seed
Pages per registrable       ≤ 8 per run (also bounded by the profile's per-domain
domain                      cap and by the budget the fetcher itself enforces)
Documents (PDF …)           ≤ 10 per run (DEEP 20), and never past the profile's PDFs
Candidate links per page    the best 40 by score
Link scope                  the SAME registrable domain (public-suffix safe). A
                            cross-domain link is followed only when it scores as a
                            DOCUMENT candidate AND its host is official: the verified
                            issuer, a regulator, a government, an association, a
                            standards body or an academic host.
Stop                        budget · depth · novelty (the last 3 pages added nothing) ·
                            ``robots_disallowed`` (skipped, counted) · a repeated URL
                            pattern (calendar and pagination traps)
==========================  ======================================================

DOCUMENT SCORING (spec §11.2) — deterministic, ``CRAWL_SCORING_VERSION``
=======================================================================
Document type by extension (PDF, PPTX, XLSX) · multilingual anchor / URL keywords
(annual report, Geschäftsbericht, rapport annuel, results presentation, whitepaper …) ·
host class of the target · a year in the URL or anchor inside the family's window ·
overlap with the family's terms · the company's name or ticker in the anchor or URL. A
URL already held (or already fetched this run) is skipped.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.services.providers.contracts import QueryFamily
from app.services.sources.safe_web_fetcher import extract_links
from app.services.traversal.issuer_site import (
    _JS_GATED_MAX_LINKS,
    _JS_GATED_MIN_BODY,
    SKIP_ALREADY_SEEN,
    SKIP_OFF_DOMAIN,
    SKIP_ROBOTS,
    STOPPED_EXHAUSTED,
    STOPPED_MAX_DEPTH,
    STOPPED_MAX_PAGES,
    VisitedPage,
    registrable_domain_of,
)
from app.services.web_research.budget import (
    BUDGET_REFUSAL_PREFIX,
    LIMIT_WALL,
    WebResearchBudget,
)
from app.services.web_research.classify import (
    SC_ACADEMIC_PAPER,
    SC_COMPANY_PRESS_RELEASE,
    SC_COMPANY_WEB_PAGE,
    SC_EXCHANGE_ANNOUNCEMENT,
    SC_GOVERNMENT_PUBLICATION,
    SC_INDUSTRY_ASSOCIATION,
    SC_INVESTOR_PRESENTATION,
    SC_ISSUER_FILING,
    SC_REGULATOR_PUBLICATION,
    SC_REGULATORY_FILING,
    SC_SPECIALIST_AGENCY,
    SC_STANDARDS_BODY,
    SC_STATISTICAL_AGENCY,
    classify_source,
)
from app.services.web_research.fetch import (
    FAILURE_ROBOTS_DISALLOWED,
    ORIGIN_CRAWL,
    STATUS_FETCHED,
    STATUS_PARTIAL,
    OpenWebFetchResult,
    WebFetchContext,
    open_web_fetch,
)
from app.services.web_research.selection import fold_tokens, host_of

CRAWL_SCORING_VERSION = "w5.1"

STOPPED_NOVELTY = "novelty"
STOPPED_BUDGET = "budget"
STOPPED_MAX_DOCUMENTS = "max_documents"

SKIP_NOT_OFFICIAL = "cross_domain_not_official"
SKIP_BELOW_THRESHOLD = "below_threshold"
SKIP_REPEATED_PATTERN = "repeated_url_pattern"
SKIP_DOMAIN_CAP = "domain_page_cap"
SKIP_DOCUMENT_CAP = "document_cap"
SKIP_FETCH_FAILED = "fetch_failed"
SKIP_NOT_HTML = "not_html"

#: Hosts whose documents a cross-domain link may reach (spec §11.1).
OFFICIAL_CLASSES: frozenset[str] = frozenset(
    {
        SC_ISSUER_FILING, SC_REGULATORY_FILING, SC_EXCHANGE_ANNOUNCEMENT,
        SC_COMPANY_PRESS_RELEASE, SC_INVESTOR_PRESENTATION, SC_COMPANY_WEB_PAGE,
        SC_GOVERNMENT_PUBLICATION, SC_REGULATOR_PUBLICATION, SC_STATISTICAL_AGENCY,
        SC_INDUSTRY_ASSOCIATION, SC_STANDARDS_BODY, SC_ACADEMIC_PAPER, SC_SPECIALIST_AGENCY,
    }
)
#: Classes that count as "host class: issuer, regulator, government, association, academic".
_HIGH_HOST_CLASSES: frozenset[str] = OFFICIAL_CLASSES

DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".pptx", ".ppt", ".xlsx", ".xls")

#: Multilingual document cues in an anchor or a URL (spec §11.2).
DOCUMENT_KEYWORDS: tuple[str, ...] = (
    "annual report", "annual-report", "annualreport", "geschäftsbericht", "geschaeftsbericht",
    "rapport annuel", "relazione annuale", "informe anual", "årsrapport", "årsredovisning",
    "vuosikertomus", "raport roczny", "jaarverslag", "integrated report",
    "interim report", "half-year", "halbjahres", "quarterly report", "quartalsmitteilung",
    "results presentation", "investor presentation", "capital markets day", "presentation",
    "präsentation", "présentation", "presentazione", "presentación", "whitepaper",
    "white paper", "technical report", "market report", "industry report", "consultation",
    "konsultation", "factsheet", "fact sheet", "study", "studie", "étude",
    "有価証券報告書", "決算説明", "年度报告", "年报",
)
#: Navigation words: worth following on the same domain, never a document on their own.
NAV_KEYWORDS: tuple[str, ...] = (
    "investor", "investors", "investor relations", "reports", "results", "publications",
    "downloads", "financial", "press", "media", "news", "presentations", "archive",
    "anleger", "investoren", "investisseurs", "investitori", "inversores", "投资者", "投資家",
)

W_EXTENSION = 3.0
W_KEYWORD = 3.0
W_NAV = 1.0
W_HOST_CLASS = 3.0
W_YEAR = 2.0
W_TERMS = 2.0
W_IDENTITY = 2.0
#: A link scoring at least this is a DOCUMENT candidate (extension or keyword, plus a
#: second signal), which is also what a cross-domain link must reach.
DOCUMENT_THRESHOLD = 4.0
#: Same-domain navigation worth following scores at least this.
FOLLOW_THRESHOLD = 1.0
MAX_REPEATED_PATTERN = 3

_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_DIGITS_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class CrawlBounds:
    """Every limit one crawl runs under. All finite."""

    max_depth: int = 2
    max_pages_per_domain: int = 8
    max_documents: int = 10
    max_links_per_page: int = 40
    #: Stop after this many consecutive fetched pages that added nothing novel.
    novelty_window: int = 3
    #: Candidate links extracted from one page before scoring keeps the best 40.
    extract_links_per_page: int = 200

    def __post_init__(self) -> None:
        for name in ("max_depth", "max_pages_per_domain", "max_documents",
                     "max_links_per_page", "novelty_window"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive: an unbounded crawl is not offered")


@dataclass(frozen=True)
class CrawlContext:
    today: date
    #: VERIFIED issuer domains only.
    issuer_domains: tuple[str, ...] = ()
    identity_terms: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    #: Years inside the family's freshness window (a year outside it scores nothing).
    window_years: frozenset[int] = frozenset()
    held_urls: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CrawlSeed:
    url: str
    family: QueryFamily = QueryFamily.COMPANY_DOCS
    #: Set when the page was already fetched (a selected search result): its links are
    #: expanded and it is not fetched again.
    fetched: OpenWebFetchResult | None = None
    result_id: Any = None


@dataclass(frozen=True)
class CrawlLink:
    url: str
    fetch_target: str
    text: str
    score: float
    depth: int
    parent_url: str
    family: QueryFamily
    is_document: bool


@dataclass
class CrawlFetch:
    """One page or document the crawl fetched (``content`` is on ``result``)."""

    link: CrawlLink
    result: OpenWebFetchResult


@dataclass
class CrawlResult:
    stopped_by: str = STOPPED_EXHAUSTED
    pages: list[VisitedPage] = field(default_factory=list)
    fetches: list[CrawlFetch] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    max_depth_reached: int = 0
    js_gated_pages: int = 0
    documents_fetched: int = 0
    novel_pages: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CRAWL_SCORING_VERSION,
            "stopped_by": self.stopped_by,
            "pages_fetched": len(self.fetches),
            "pages_expanded": len(self.pages),
            "documents_fetched": self.documents_fetched,
            "novel_pages": self.novel_pages,
            "max_depth_reached": self.max_depth_reached,
            "js_gated_pages": self.js_gated_pages,
            "skipped": dict(sorted(self.skipped.items())),
        }


# --------------------------------------------------------------------------- #
# Scoring (spec §11.2)
# --------------------------------------------------------------------------- #


def is_document_url(url: str) -> bool:
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return False
    return path.endswith(DOCUMENT_EXTENSIONS)


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    return any(n in haystack for n in needles)


def score_link(url: str, text: str, ctx: CrawlContext) -> tuple[float, bool]:
    """``(score, is_document_candidate)`` for one link. Deterministic."""
    haystack = f"{text or ''} {url}".casefold()
    score = 0.0
    document = False
    if is_document_url(url):
        score += W_EXTENSION
        document = True
    if _contains_any(haystack, DOCUMENT_KEYWORDS):
        score += W_KEYWORD
        document = True
    elif _contains_any(haystack, NAV_KEYWORDS):
        score += W_NAV
    host = host_of(url)
    if host:
        klass = classify_source(url, issuer_domains=ctx.issuer_domains).source_class
        if klass in _HIGH_HOST_CLASSES:
            score += W_HOST_CLASS
    years = {int(m.group(0)) for m in _YEAR_RE.finditer(haystack)}
    if years & set(ctx.window_years):
        score += W_YEAR
    wanted = {t for term in ctx.query_terms for t in fold_tokens(term)}
    if wanted:
        score += W_TERMS * (len(wanted & fold_tokens(haystack)) / len(wanted))
    identity = fold_tokens(haystack)
    if any(fold_tokens(t) and fold_tokens(t) <= identity for t in ctx.identity_terms):
        score += W_IDENTITY
    return round(score, 4), document


def url_pattern(url: str) -> str:
    """A calendar/pagination signature: digits collapsed, query reduced to its keys."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    keys = sorted({k for k, _v in parse_qsl(parts.query, keep_blank_values=True)})
    return f"{(parts.hostname or '').lower()}{_DIGITS_RE.sub('#', parts.path)}?{','.join(keys)}"


def official_host(host: str, ctx: CrawlContext) -> bool:
    """Spec §11.1: a cross-domain link may reach an official host and nothing else."""
    klass = classify_source(f"https://{host}/", issuer_domains=ctx.issuer_domains).source_class
    return klass in OFFICIAL_CLASSES


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #

FetchFn = Callable[..., Awaitable[OpenWebFetchResult]]
#: ``on_fetched(result, link)`` → True when the page added something novel (a stored
#: document, a new chunk), False when it added nothing, None when it is not the kind of
#: page that adds chunks (navigation). Three False answers in a row stop the crawl.
OnFetched = Callable[[OpenWebFetchResult, CrawlLink], Awaitable[bool | None]]


def _html_of(result: OpenWebFetchResult) -> str | None:
    if result.status not in (STATUS_FETCHED, STATUS_PARTIAL) or result.content is None:
        return None
    return result.text()


def _links_of(
    html: str, base_url: str, *, page_domain: str, depth: int, parent: str,
    family: QueryFamily, ctx: CrawlContext, bounds: CrawlBounds, result: CrawlResult,
    seen: set[str], same_domain_only: bool = False,
) -> list[CrawlLink]:
    allowed = (page_domain,) if page_domain else ()
    anchors = extract_links(
        html,
        base_url=base_url,
        allowed_domains=allowed,
        keywords=("",),
        max_links=bounds.extract_links_per_page,
        host_ok=None if same_domain_only else (lambda host: official_host(host, ctx)),
    )
    out: list[CrawlLink] = []
    for link in anchors:
        if link.url in seen or link.url in ctx.held_urls:
            result.skip(SKIP_ALREADY_SEEN)
            continue
        score, document = score_link(link.url, link.text, ctx)
        link_domain = registrable_domain_of(link.url)
        if link_domain != page_domain:
            # Cross-domain: a document candidate on an official host, nothing else.
            if not document or score < DOCUMENT_THRESHOLD:
                result.skip(SKIP_OFF_DOMAIN)
                continue
            if not official_host(host_of(link.url), ctx):
                result.skip(SKIP_NOT_OFFICIAL)
                continue
        elif score < FOLLOW_THRESHOLD and not document:
            result.skip(SKIP_BELOW_THRESHOLD)
            continue
        out.append(
            CrawlLink(link.url, link.fetch_target, link.text, score, depth, parent, family,
                      document)
        )
    out.sort(key=lambda c: (-c.score, c.url))
    return out[: bounds.max_links_per_page]


async def crawl(
    seeds: Sequence[CrawlSeed],
    *,
    session: Any,
    context: WebFetchContext,
    budget: WebResearchBudget,
    ctx: CrawlContext,
    on_fetched: OnFetched | None = None,
    bounds: CrawlBounds | None = None,
    fetch: FetchFn | None = None,
    fetch_kwargs: Mapping[str, Any] | None = None,
) -> CrawlResult:
    """Walk outward from ``seeds`` within ``bounds`` and ``budget``. Never raises.

    Every fetch is ``open_web_fetch(origin="crawl")``. A seed that already carries a
    fetched page is expanded without being fetched again.
    """
    bounds = bounds or CrawlBounds()
    fetch_fn = fetch or open_web_fetch
    extra = dict(fetch_kwargs or {})
    result = CrawlResult()
    seen: set[str] = set()
    pattern_counts: dict[str, int] = {}
    domain_pages: dict[str, int] = {}
    # (depth, -score, url, tiebreak) so the walk is breadth-first and deterministic.
    frontier: list[tuple[int, float, str, int, CrawlLink]] = []
    counter = 0
    non_novel_streak = 0
    doc_cap = max(0, min(bounds.max_documents, budget.limits.max_pdfs - budget.pdfs))

    def push(link: CrawlLink) -> None:
        nonlocal counter
        counter += 1
        heapq.heappush(frontier, (link.depth, -link.score, link.url, counter, link))

    async def expand(page: OpenWebFetchResult, *, url: str, depth: int,
                     family: QueryFamily) -> None:
        html = _html_of(page)
        if html is None or page.content_class != "html":
            return
        base = page.final_url or page.requested_url or url
        domain = registrable_domain_of(base)
        visited = VisitedPage(url=url, depth=depth, status_code=page.http_status)
        result.pages.append(visited)
        result.max_depth_reached = max(result.max_depth_reached, depth)
        links = _links_of(
            html, base, page_domain=domain or "", depth=depth + 1, parent=url, family=family,
            ctx=ctx, bounds=bounds, result=result, seen=seen,
        ) if domain and depth < bounds.max_depth else []
        visited.link_count = len(links)
        visited.document_link_count = sum(1 for link in links if link.is_document)
        # The shape of a JS-rendered page: a long body and almost no anchors (or the
        # fetcher said so). Recorded, never worked around — there is no browser (W10).
        if bool(page.js_required) or (
            len(html) >= _JS_GATED_MIN_BODY and visited.link_count <= _JS_GATED_MAX_LINKS
        ):
            visited.appears_js_gated = True
            result.js_gated_pages += 1
        for link in links:
            seen.add(link.url)
            push(link)

    for seed in seeds:
        url = seed.url
        if not url or url in seen:
            continue
        seen.add(url)
        if seed.fetched is not None:
            domain = registrable_domain_of(url) or ""
            domain_pages[domain] = domain_pages.get(domain, 0) + 1
            await expand(seed.fetched, url=url, depth=0, family=seed.family)
        else:
            push(CrawlLink(url, url, "", 0.0, 0, "", seed.family, is_document_url(url)))

    while frontier:
        _depth, _neg, _url, _n, link = heapq.heappop(frontier)
        refusal = budget.fetch_refusal(is_pdf=link.is_document and is_document_url(link.url))
        if refusal is not None:
            result.stopped_by = (
                STOPPED_BUDGET if refusal != BUDGET_REFUSAL_PREFIX + LIMIT_WALL else LIMIT_WALL
            )
            result.skip(refusal)
            break
        if link.is_document and result.documents_fetched >= doc_cap:
            result.skip(SKIP_DOCUMENT_CAP)
            if result.documents_fetched >= bounds.max_documents:
                result.stopped_by = STOPPED_MAX_DOCUMENTS
            continue
        domain = registrable_domain_of(link.url) or host_of(link.url)
        if domain_pages.get(domain, 0) >= bounds.max_pages_per_domain:
            result.skip(SKIP_DOMAIN_CAP)
            continue
        pattern = url_pattern(link.url)
        if pattern_counts.get(pattern, 0) >= MAX_REPEATED_PATTERN:
            result.skip(SKIP_REPEATED_PATTERN)
            continue
        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        domain_pages[domain] = domain_pages.get(domain, 0) + 1

        try:
            page = await fetch_fn(
                session, link.fetch_target, context=context, budget=budget,
                origin=ORIGIN_CRAWL, **extra,
            )
        except Exception:  # noqa: BLE001 - one bad page must not end the walk
            result.skip(SKIP_FETCH_FAILED)
            continue
        if page.failure_code == FAILURE_ROBOTS_DISALLOWED:
            # Skipped, never fetched-and-discarded: the request did not happen.
            result.skip(SKIP_ROBOTS)
            continue
        if page.status not in (STATUS_FETCHED, STATUS_PARTIAL) or page.content is None:
            result.skip(page.failure_code or SKIP_FETCH_FAILED)
            continue
        result.fetches.append(CrawlFetch(link, page))
        if page.content_class != "html":
            result.documents_fetched += 1
        novel: bool | None = True
        if on_fetched is not None:
            try:
                novel = await on_fetched(page, link)
            except Exception:  # noqa: BLE001 - ingestion trouble is not a crawl failure
                novel = False
        if novel is None:
            pass  # neutral: a navigation page neither adds to nor spends the streak
        elif novel:
            result.novel_pages += 1
            non_novel_streak = 0
        else:
            non_novel_streak += 1
        await expand(page, url=link.url, depth=link.depth, family=link.family)
        if non_novel_streak >= bounds.novelty_window:
            result.stopped_by = STOPPED_NOVELTY
            break

    if result.stopped_by == STOPPED_EXHAUSTED and frontier:
        result.stopped_by = STOPPED_MAX_PAGES
    return result


__all__ = [
    "CRAWL_SCORING_VERSION",
    "DOCUMENT_KEYWORDS",
    "DOCUMENT_THRESHOLD",
    "OFFICIAL_CLASSES",
    "STOPPED_BUDGET",
    "STOPPED_MAX_DEPTH",
    "STOPPED_MAX_DOCUMENTS",
    "STOPPED_NOVELTY",
    "CrawlBounds",
    "CrawlContext",
    "CrawlFetch",
    "CrawlLink",
    "CrawlResult",
    "CrawlSeed",
    "crawl",
    "is_document_url",
    "official_host",
    "score_link",
    "url_pattern",
]
