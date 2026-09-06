"""V3.4 Slice 4.10 — bounded issuer-site traversal.

Every test here is about a bound, a refusal, or an honest report of how far the walk got.
An unbounded walk of somebody else's site is the thing the module exists to make
impossible, and a walk that stopped at a cap and did not say so is the thing that makes a
reader conclude the site has twelve pages.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.services.sources.safe_web_fetcher import SafeFetchResult
from app.services.traversal.issuer_site import (
    ROBOTS_AGENT,
    SKIP_BLOCKED,
    SKIP_FETCH_FAILED,
    SKIP_ROBOTS,
    STOPPED_DISABLED,
    STOPPED_EXHAUSTED,
    STOPPED_MAX_PAGES,
    STOPPED_MAX_SECONDS,
    TraversalBounds,
    registrable_domain_of,
    traverse_issuer_site,
)

START = "https://issuer.example/investors"


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"v3_issuer_traversal_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _page(*links: str, body_extra: str = "", title: str = "IR") -> str:
    anchors = "".join(f'<a href="{href}">annual report</a>' for href in links)
    return f"<html><head><title>{title}</title></head><body>{anchors}{body_extra}</body></html>"


class _Site:
    """A fake site. Real HTML, real link extraction — only the network is absent."""

    def __init__(
        self,
        pages: dict[str, str],
        *,
        robots: str | None = None,
        blocked: set[str] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.pages = pages
        self.robots = robots
        self.blocked = blocked or set()
        self.failing = failing or set()
        self.requested: list[str] = []

    async def __call__(
        self,
        url: str,
        *,
        allowed_domains: tuple[str, ...],
        keywords: tuple[str, ...] = (),
        cfg: Any = None,
        resolve_ip: bool = False,
        **kwargs: Any,
    ) -> SafeFetchResult:
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                return SafeFetchResult(requested_url=url, error="404")
            return SafeFetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                body_html=self.robots,
            )
        if url in self.blocked:
            return SafeFetchResult(
                requested_url=url, blocked=True, error="host not in allowlist"
            )
        if url in self.failing:
            return SafeFetchResult(requested_url=url, status_code=500, error="http 500")
        body = self.pages.get(url)
        if body is None:
            return SafeFetchResult(requested_url=url, status_code=404, error="http 404")
        return SafeFetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            title="IR",
            body_html=body,
        )


class TestBounds:
    def test_a_bound_of_zero_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError, match="somebody else's site"):
            TraversalBounds(max_pages=0)

    async def test_the_walk_is_off_by_default_and_makes_no_request(self) -> None:
        site = _Site({START: _page()})
        result = await traverse_issuer_site(START, cfg=Settings(), fetcher=site)
        assert result.stopped_by == STOPPED_DISABLED
        assert site.requested == []

    async def test_a_page_cap_stops_the_walk_and_says_so(self) -> None:
        """A result reporting "2 pages" without saying it hit its cap invites a reader
        to conclude the site has two pages."""
        pages = {
            START: _page(*[f"https://issuer.example/p{i}" for i in range(10)]),
            **{f"https://issuer.example/p{i}": _page() for i in range(10)},
        }
        result = await traverse_issuer_site(
            START,
            cfg=_cfg(),
            bounds=TraversalBounds(max_pages=3),
            fetcher=_Site(pages),
        )
        assert result.stopped_by == STOPPED_MAX_PAGES
        assert len(result.pages) == 3
        assert result.completed is False
        assert result.frontier_remaining > 0

    async def test_a_walk_that_empties_its_frontier_is_complete(self) -> None:
        pages = {START: _page("https://issuer.example/a"), "https://issuer.example/a": _page()}
        result = await traverse_issuer_site(
            START, cfg=_cfg(), bounds=TraversalBounds(max_pages=10), fetcher=_Site(pages)
        )
        assert result.stopped_by == STOPPED_EXHAUSTED
        assert result.completed is True
        assert result.frontier_remaining == 0

    async def test_depth_bounds_how_far_the_walk_goes(self) -> None:
        pages = {
            START: _page("https://issuer.example/a"),
            "https://issuer.example/a": _page("https://issuer.example/b"),
            "https://issuer.example/b": _page("https://issuer.example/c"),
            "https://issuer.example/c": _page(),
        }
        result = await traverse_issuer_site(
            START, cfg=_cfg(), bounds=TraversalBounds(max_depth=1), fetcher=_Site(pages)
        )
        assert {p.url for p in result.pages} == {START, "https://issuer.example/a"}
        # The walk finished its frontier: depth is a range limit, not a stop.
        assert result.stopped_by == STOPPED_EXHAUSTED

    async def test_a_clock_bound_stops_the_walk(self) -> None:
        pages = {
            START: _page(*[f"https://issuer.example/p{i}" for i in range(5)]),
            **{f"https://issuer.example/p{i}": _page() for i in range(5)},
        }
        ticks = iter([0.0, 0.0, 0.0, 99.0, 99.0, 99.0])

        result = await traverse_issuer_site(
            START,
            cfg=_cfg(),
            bounds=TraversalBounds(max_seconds=10.0),
            fetcher=_Site(pages),
            now=lambda: next(ticks, 99.0),
        )
        assert result.stopped_by == STOPPED_MAX_SECONDS

    async def test_links_per_page_are_capped(self) -> None:
        pages = {
            START: _page(*[f"https://issuer.example/p{i}" for i in range(50)]),
        }
        result = await traverse_issuer_site(
            START,
            cfg=_cfg(),
            bounds=TraversalBounds(max_links_per_page=4, max_pages=1),
            fetcher=_Site(pages),
        )
        assert result.pages[0].link_count == 4


class TestGuards:
    async def test_robots_disallow_is_obeyed_by_not_requesting(self) -> None:
        """"We obeyed robots.txt" has to mean the request did not happen."""
        pages = {
            START: _page("https://issuer.example/private/x"),
            "https://issuer.example/private/x": _page(),
        }
        site = _Site(pages, robots="User-agent: *\nDisallow: /private/")
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert result.skipped.get(SKIP_ROBOTS) == 1
        assert "https://issuer.example/private/x" not in site.requested
        assert result.robots_consulted is True

    async def test_an_unreachable_robots_is_not_a_prohibition(self) -> None:
        site = _Site({START: _page()}, robots=None)
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert result.robots_consulted is False
        assert len(result.pages) == 1

    async def test_the_agent_robots_is_evaluated_against_is_named(self) -> None:
        pages = {START: _page("https://issuer.example/x"), "https://issuer.example/x": _page()}
        site = _Site(
            pages, robots=f"User-agent: {ROBOTS_AGENT}\nDisallow: /x"
        )
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert result.skipped.get(SKIP_ROBOTS) == 1

    async def test_a_blocked_page_is_counted_not_swallowed(self) -> None:
        pages = {START: _page("https://issuer.example/a"), "https://issuer.example/a": _page()}
        site = _Site(pages, blocked={"https://issuer.example/a"})
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert result.skipped.get(SKIP_BLOCKED) == 1

    async def test_one_bad_page_does_not_end_the_walk(self) -> None:
        pages = {
            START: _page("https://issuer.example/a", "https://issuer.example/b"),
            "https://issuer.example/a": _page(),
            "https://issuer.example/b": _page(),
        }
        site = _Site(pages, failing={"https://issuer.example/a"})
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert result.skipped.get(SKIP_FETCH_FAILED) == 1
        assert "https://issuer.example/b" in {p.url for p in result.pages}

    async def test_an_off_domain_link_is_not_followed(self) -> None:
        pages = {START: _page("https://contentfarm.example/summary")}
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=_Site(pages))
        assert {p.url for p in result.pages} == {START}

    async def test_an_off_domain_cdn_must_be_named_explicitly(self) -> None:
        """Pandora's documents live on one. Inferring it from a redirect would let any
        site widen its own allowlist."""
        pages = {
            START: _page("https://cdn.example/annual-2025"),
            "https://cdn.example/annual-2025": _page(),
        }
        without = await traverse_issuer_site(START, cfg=_cfg(), fetcher=_Site(pages))
        assert {p.url for p in without.pages} == {START}
        with_cdn = await traverse_issuer_site(
            START, cfg=_cfg(), extra_domains=("cdn.example",), fetcher=_Site(pages)
        )
        assert "https://cdn.example/annual-2025" in {p.url for p in with_cdn.pages}

    async def test_a_non_https_start_url_is_refused(self) -> None:
        site = _Site({})
        result = await traverse_issuer_site(
            "http://issuer.example/ir", cfg=_cfg(), fetcher=site
        )
        assert result.pages == []

    def test_an_internal_host_has_no_registrable_domain(self) -> None:
        assert registrable_domain_of("https://localhost/ir") is None
        assert registrable_domain_of("https://10.0.0.1/ir") is None
        assert registrable_domain_of("https://ir.issuer.example/x") == "issuer.example"


class TestFindings:
    async def test_document_links_are_collected_and_never_fetched(self) -> None:
        """Turning a URL into bytes with a hash is the document fetcher's job, and its
        guards are the ones that matter for a 30 MB PDF."""
        pages = {START: _page("https://issuer.example/ar2025.pdf")}
        site = _Site(pages)
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=site)
        assert [link.url for link in result.document_links] == [
            "https://issuer.example/ar2025.pdf"
        ]
        assert "https://issuer.example/ar2025.pdf" not in site.requested

    async def test_a_document_found_twice_is_listed_once(self) -> None:
        pages = {
            START: _page("https://issuer.example/a", "https://issuer.example/ar.pdf"),
            "https://issuer.example/a": _page("https://issuer.example/ar.pdf"),
        }
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=_Site(pages))
        assert len(result.document_links) == 1

    async def test_a_js_gated_page_is_recorded_not_worked_around(self) -> None:
        """Phase 32A slice 5A measured the alternative: zero successful native
        extractions across seven live issuers, success path only in unit tests."""
        pages = {START: _page(body_extra="<div>" + ("x" * 30_000) + "</div>")}
        result = await traverse_issuer_site(START, cfg=_cfg(), fetcher=_Site(pages))
        assert result.pages[0].appears_js_gated is True
        assert result.partially_inaccessible is True
        assert any("browser" in note for note in result.notes)

    async def test_an_ordinary_page_is_not_called_js_gated(self) -> None:
        pages = {
            START: _page(
                *[f"https://issuer.example/p{i}" for i in range(6)],
                body_extra="<p>" + ("y" * 30_000) + "</p>",
            ),
            **{f"https://issuer.example/p{i}": _page() for i in range(6)},
        }
        result = await traverse_issuer_site(
            START, cfg=_cfg(), bounds=TraversalBounds(max_pages=1), fetcher=_Site(pages)
        )
        assert result.pages[0].appears_js_gated is False

    async def test_the_result_serialises_without_a_secret(self) -> None:
        pages = {START: _page("https://issuer.example/ar.pdf")}
        payload = (
            await traverse_issuer_site(START, cfg=_cfg(), fetcher=_Site(pages))
        ).to_dict()
        assert payload["stopped_by"]
        assert payload["completed"] is True
        assert "body_html" not in payload
        assert set(payload) >= {
            "pages_visited",
            "document_links",
            "skipped",
            "robots_consulted",
            "partially_inaccessible",
        }
