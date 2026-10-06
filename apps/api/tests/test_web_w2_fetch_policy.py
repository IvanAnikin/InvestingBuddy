"""Open-web W2 — the open-web fetch policy (spec §9.2, §18, §20.2-20.3, §21, §22.1).

FULLY OFFLINE, like the W0 suite: names resolve through a fake ``getaddrinfo``-shaped
resolver, and every request goes through the REAL pinned transport whose inner transport
is an ``httpx.MockTransport`` that records what reached a target. "Refused before any
request" is asserted as "the mock network recorded no request to that host/address".

Pacing and retries run on a fake clock with a fake sleep: intervals are asserted, never
waited for. Rows are written to in-memory SQLite (JSONB compiled as JSON); the real
PostgreSQL types are proven in ``test_web_w2_fetch_policy_postgres.py``.

Every config below sets ``source_connector_allowlist_only=True`` (the production value):
the open-web policy must replace the allowlist WITHOUT the global switch being flipped.
"""

from __future__ import annotations

import asyncio
import codecs
import gzip
import hashlib
import inspect
import json
import logging
import random
import socket
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.web_research import WebFetchAttempt
from app.services.sources import pinned_transport as pinned_module
from app.services.sources.rate_limit import SlidingWindowLimiter
from app.services.sources.safe_web_fetcher import (
    FetchPolicy,
    async_check_fetch_url,
    read_bounded_body,
)
from app.services.web_research import access, content, robots
from app.services.web_research.audit import web_research_audit
from app.services.web_research.budget import PROFILES, WebResearchBudget
from app.services.web_research.canonical import (
    canonical_url,
    choose_canonical,
    rel_canonical_from_header,
)
from app.services.web_research.domain_policy import DENYLIST_VERSION, denylisted
from app.services.web_research.fetch import (
    STATUS_DISABLED,
    STATUS_FAILED,
    STATUS_FETCHED,
    STATUS_NEGATIVE_CACHED,
    STATUS_NOT_RETRIEVABLE,
    STATUS_PARTIAL,
    STATUS_REFUSED,
    STATUS_RETRIED,
    OpenWebFetchRuntime,
    WebFetchContext,
    open_web_fetch,
    parse_retry_after,
    reset_default_runtime,
)
from app.services.web_research.limiter import OpenWebLimiter
from app.services.web_research.negative_cache import (
    FIRST_TTL_SECONDS,
    REPEAT_TTL_SECONDS,
    NegativeCache,
)
from app.services.web_research.user_urls import extract_user_urls, fetch_user_urls

PUBLIC_A = "93.184.216.34"
PUBLIC_B = "93.184.216.35"
PRIVATE = "10.0.0.5"

HTML_PAGE = (
    b"<!doctype html><html><head><title>Transformer demand</title></head><body>"
    + b"<p>Grid operators ordered more large power transformers this year.</p>" * 20
    + b"</body></html>"
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN201
    return "JSON"


# --------------------------------------------------------------------------- #
# Offline network, fake clock, session
# --------------------------------------------------------------------------- #


def _info(ip: str) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr: tuple[Any, ...] = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


class FakeDNS:
    """``getaddrinfo``-shaped. A tuple value is a sequence of answers (rebinding)."""

    def __init__(self, table: dict[str, Any] | None = None) -> None:
        self.table: dict[str, Any] = {
            "example.com": [PUBLIC_A],
            "www.example.com": [PUBLIC_A],
            "news.example.org": [PUBLIC_B],
            "other.example.net": [PUBLIC_B],
            "a.example": [PUBLIC_A],
            "b.example": [PUBLIC_A],
            "c.example": [PUBLIC_A],
            "d.example": [PUBLIC_A],
            "e.example": [PUBLIC_A],
            **(table or {}),
        }
        self.calls: list[str] = []
        self._served: dict[str, int] = {}

    def __call__(self, host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
        self.calls.append(host)
        entry = self.table.get(host)
        if entry is None:
            raise socket.gaierror(f"no such host: {host}")
        if isinstance(entry, tuple):
            n = self._served.get(host, 0)
            self._served[host] = n + 1
            answer = entry[min(n, len(entry) - 1)]
        else:
            answer = entry
        return [_info(ip) for ip in answer]


def respond(
    status: int = 200,
    body: bytes = HTML_PAGE,
    *,
    content_type: str | None = "text/html; charset=utf-8",
    **headers: str,
) -> Any:
    """A route factory: a fresh ``httpx.Response`` per request."""
    hdrs = {k.replace("_", "-"): v for k, v in headers.items()}
    if content_type:
        hdrs["content-type"] = content_type

    def route(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=hdrs, content=body)

    return route


def redirect(location: str, status: int = 302) -> Any:
    def route(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"location": location})

    return route


def streamed(headers: dict[str, str], body: bytes, chunk: int = 16_384) -> Any:
    def route(_request: httpx.Request) -> httpx.Response:
        async def gen():  # noqa: ANN202
            for i in range(0, len(body), chunk):
                yield body[i : i + chunk]

        return httpx.Response(200, headers=headers, content=gen())

    return route


def connect_error(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=_request)


class Web:
    """Routes by (Host header, path). Unknown paths answer 404. Records every request."""

    def __init__(self) -> None:
        self.sites: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def site(self, host: str, **routes: Any) -> None:
        self.sites.setdefault(host, {}).update(routes)

    def route(self, host: str, path: str, handler: Any) -> None:
        self.sites.setdefault(host, {})[path] = handler

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", "")
        route = self.sites.get(host, {}).get(request.url.path)
        if route is None:
            return httpx.Response(404, content=b"not found")
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        response = route(request)
        if inspect.isawaitable(response):
            response = await response
        return response

    def hosts(self) -> list[str]:
        return [r.headers.get("host", "") for r in self.requests]

    def ips(self) -> list[str]:
        return [r.url.host for r in self.requests]

    def paths(self, host: str) -> list[str]:
        return [r.url.path for r in self.requests if r.headers.get("host") == host]

    def page_paths(self, host: str) -> list[str]:
        return [p for p in self.paths(host) if p not in ("/robots.txt", robots.TDMREP_PATH)]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    network = Web()

    def _build(pins: dict[str, str] | None = None, **_kw: Any) -> Any:
        return pinned_module.PinnedAsyncHTTPTransport(
            pins=pins, transport_factory=lambda: httpx.MockTransport(network.handler)
        )

    monkeypatch.setattr(pinned_module, "build_pinned_transport", _build)
    return network


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)
        await asyncio.sleep(0)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def runtime(clock: FakeClock) -> OpenWebFetchRuntime:
    return OpenWebFetchRuntime.create(clock=clock, sleep=clock.sleep, rng=random.Random(7))


@pytest.fixture(autouse=True)
def _fresh_default_runtime() -> Any:
    reset_default_runtime()
    yield
    reset_default_runtime()


_WEB_TABLES = ("web_search_queries", "web_search_results", "web_fetch_attempts")


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    tables = [Base.metadata.tables[name] for name in _WEB_TABLES]
    async with engine.begin() as conn:
        # Only the three web tables: SQLite does not validate FK targets at CREATE time,
        # and building the whole schema per test dominated this file's runtime.
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _cfg(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "v3_web_fetch_enabled": True,
        "source_connector_allowlist_only": True,  # production value: NOT flipped
        "primary_document_pin_dns_enabled": True,
        "source_connector_timeout_seconds": 10,
        "source_fetch_total_deadline_seconds": 30.0,
        "source_document_total_deadline_seconds": 60.0,
    }
    values.update(overrides)
    return Settings(**values)


def _budget(clock: FakeClock, profile: str = "company_standard") -> WebResearchBudget:
    return WebResearchBudget(limits=PROFILES[profile], daily_cap=300, clock=clock)


class Harness:
    def __init__(
        self,
        session: Any,
        web: Web,
        runtime: OpenWebFetchRuntime,
        clock: FakeClock,
        dns: FakeDNS | None = None,
    ) -> None:
        self.session = session
        self.web = web
        self.runtime = runtime
        self.clock = clock
        self.dns = dns or FakeDNS()
        self.budget = _budget(clock)
        self.job_id = uuid.uuid4()
        self.cfg = _cfg()

    async def fetch(self, url: str, origin: str = "search", **kw: Any) -> Any:
        return await open_web_fetch(
            self.session,
            url,
            context=WebFetchContext(research_job_id=self.job_id),
            budget=kw.pop("budget", self.budget),
            origin=origin,
            cfg=kw.pop("cfg", self.cfg),
            resolver=self.dns,
            runtime=self.runtime,
            **kw,
        )

    async def all_rows(self) -> list[WebFetchAttempt]:
        result = await self.session.execute(
            select(WebFetchAttempt).order_by(WebFetchAttempt.created_at)
        )
        return list(result.scalars().all())

    async def rows(self) -> list[WebFetchAttempt]:
        """Page-attempt rows (robots.txt / TDMRep rows excluded)."""
        return [r for r in await self.all_rows() if r.origin not in ("robots", "tdm")]


@pytest.fixture
def h(session: Any, web: Web, runtime: OpenWebFetchRuntime, clock: FakeClock) -> Harness:
    return Harness(session, web, runtime, clock)


# --------------------------------------------------------------------------- #
# The flag and the happy path
# --------------------------------------------------------------------------- #


class TestTheFlag:
    async def test_flag_off_is_a_refusal_with_no_network_and_no_row(self, h: Harness) -> None:
        h.web.site("example.com", **{"/": respond()})
        result = await h.fetch("https://example.com/", cfg=_cfg(v3_web_fetch_enabled=False))
        assert result.status == STATUS_DISABLED
        assert result.failure_code == "web_fetch_disabled"
        assert h.dns.calls == []
        assert h.web.requests == []
        assert await h.rows() == []

    async def test_the_default_setting_is_off(self) -> None:
        assert Settings.model_fields["v3_web_fetch_enabled"].default is False

    async def test_an_unknown_origin_is_a_programming_error(self, h: Harness) -> None:
        with pytest.raises(ValueError):
            await h.fetch("https://example.com/", origin="browser")


class TestHappyPath:
    async def test_a_public_page_is_fetched_through_the_pin_with_a_complete_row(
        self, h: Harness
    ) -> None:
        h.web.site(
            "example.com",
            **{"/report": respond(etag='"v1"', last_modified="Wed, 30 Sep 2026 10:00:00 GMT")},
        )
        result_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        result = await h.fetch(
            "https://example.com/report?utm_source=news&token=s3cret&id=7",
            search_result_id=result_id,
            parent_attempt_id=parent_id,
        )
        assert result.status == STATUS_FETCHED and result.complete and result.ok
        assert result.content == HTML_PAGE
        # Pinned: every request connected to the validated address.
        assert set(h.web.ips()) == {PUBLIC_A}
        page = [r for r in h.web.requests if r.url.path == "/report"][0]
        # D12: the URL requested is the URL given (token and utm kept on the wire) …
        assert b"token=s3cret" in page.url.query and b"utm_source=news" in page.url.query
        assert page.headers["user-agent"].startswith("InvestingBuddy-Research-Bot/")
        assert "cookie" not in page.headers and "authorization" not in page.headers

        rows = await h.rows()
        assert len(rows) == 1
        row = rows[0]
        # … while every STORED url is secret-stripped, and the canonical drops tracking.
        assert "s3cret" not in row.requested_url and "s3cret" not in (row.final_url or "")
        assert row.canonical_url == "https://example.com/report?id=7"
        assert row.origin == "search"
        assert row.research_job_id == h.job_id
        assert row.web_search_result_id == result_id
        assert row.parent_attempt_id == parent_id
        assert row.policy_decision == "allowed"
        assert row.robots_decision == robots.ROBOTS_NONE
        assert row.tdm_decision == robots.TDM_NOT_RESERVED
        assert row.http_status == 200
        assert row.mime_served == "text/html" and row.mime_sniffed == "text/html"
        assert row.bytes == len(HTML_PAGE)
        assert row.truncated is False
        assert row.content_hash == hashlib.sha256(HTML_PAGE).hexdigest()
        assert isinstance(row.fetch_ms, int)
        assert row.status == "fetched" and row.failure_code is None
        meta = row.redirect_chain_json[-1]["meta"]
        assert meta["etag"] == '"v1"'
        assert meta["last_modified"].startswith("Wed, 30 Sep 2026")
        assert meta["charset"] == "utf-8" and meta["charset_source"] == "header"
        assert result.attempt_id == row.id

    async def test_the_allowlist_is_replaced_by_policy_not_by_the_global_switch(self) -> None:
        cfg = _cfg()
        dns = FakeDNS()
        reason, ip = await async_check_fetch_url(
            "https://example.com/", (), cfg=cfg, resolver=dns, policy=FetchPolicy.OPEN_WEB
        )
        assert reason is None and ip == PUBLIC_A
        # The same URL under the historical policy is still refused by the allowlist.
        reason, _ = await async_check_fetch_url("https://example.com/", (), cfg=cfg, resolver=dns)
        assert reason is not None and "allowlist" in reason

    async def test_logs_carry_codes_never_urls_or_page_text(
        self, h: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        h.web.site("example.com", **{"/secret-path": respond()})
        with caplog.at_level(logging.INFO):
            await h.fetch("https://example.com/secret-path?q=private-thing")
        text = caplog.text
        assert "web_fetch_attempt" in text
        assert "secret-path" not in text and "private-thing" not in text
        assert "example.com" not in text and "Grid operators" not in text


# --------------------------------------------------------------------------- #
# SSRF, re-run through the policy (SSRF-10…15) + the domain denylist
# --------------------------------------------------------------------------- #


class TestRedirectSsrfThroughThePolicy:
    async def test_ssrf10_redirect_to_loopback_is_refused_before_any_request(
        self, h: Harness
    ) -> None:
        h.web.site("example.com", **{"/go": redirect("https://127.0.0.1/admin")})
        result = await h.fetch("https://example.com/go")
        assert result.status == STATUS_REFUSED and result.policy_decision == "denied"
        assert result.failure_code == "blocked_host"
        assert "127.0.0.1" not in h.web.ips() + h.web.hosts()
        assert result.redirect_chain[-1]["refused"] == "blocked_host"

    async def test_ssrf11_https_to_http_downgrade_is_refused(self, h: Harness) -> None:
        h.web.site("example.com", **{"/go": redirect("http://news.example.org/x")})
        result = await h.fetch("https://example.com/go")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "blocked_scheme"
        assert "news.example.org" not in h.web.hosts()

    async def test_ssrf12_a_rebinding_name_never_reaches_the_private_address(
        self, session: Any, web: Web, runtime: OpenWebFetchRuntime, clock: FakeClock
    ) -> None:
        dns = FakeDNS({"rebind.example": ([PUBLIC_A], [PRIVATE])})
        h = Harness(session, web, runtime, clock, dns)
        web.site("a.example", **{"/go": redirect("https://rebind.example/x")})
        web.site("rebind.example", **{"/x": respond()})
        # Hop 1 is fine; the redirect target resolves public ONCE (hop check), then private.
        result = await h.fetch("https://a.example/go")
        assert PRIVATE not in web.ips()
        # Every request that happened went to a validated public address.
        assert set(web.ips()) <= {PUBLIC_A}
        assert result.status in (STATUS_REFUSED, STATUS_FETCHED)
        assert dns.calls.count("rebind.example") >= 2  # re-resolved per use, never reused

    async def test_ssrf12_a_redirect_hop_is_re_resolved_and_re_validated(
        self, session: Any, web: Web, runtime: OpenWebFetchRuntime, clock: FakeClock
    ) -> None:
        dns = FakeDNS({"rebind.example": ([PRIVATE],)})
        h = Harness(session, web, runtime, clock, dns)
        web.site("a.example", **{"/go": redirect("https://rebind.example/x")})
        result = await h.fetch("https://a.example/go")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "blocked_private_ip"
        assert "rebind.example" not in web.hosts() and PRIVATE not in web.ips()

    async def test_ssrf13_one_public_and_one_private_record_is_refused(
        self, session: Any, web: Web, runtime: OpenWebFetchRuntime, clock: FakeClock
    ) -> None:
        dns = FakeDNS({"mixed.example": [PUBLIC_A, PRIVATE]})
        h = Harness(session, web, runtime, clock, dns)
        result = await h.fetch("https://mixed.example/")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "blocked_private_ip"
        assert web.requests == []

    async def test_ssrf14_a_four_hop_chain_is_refused_at_hop_four(self, h: Harness) -> None:
        h.web.site("a.example", **{"/": redirect("https://b.example/")})
        h.web.site("b.example", **{"/": redirect("https://c.example/")})
        h.web.site("c.example", **{"/": redirect("https://d.example/")})
        h.web.site("d.example", **{"/": redirect("https://e.example/")})
        h.web.site("e.example", **{"/": respond()})
        result = await h.fetch("https://a.example/")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "redirect_limit"
        assert h.web.page_paths("e.example") == []

    async def test_ssrf14_a_redirect_loop_is_refused(self, h: Harness) -> None:
        h.web.site("a.example", **{"/": redirect("https://b.example/")})
        h.web.site("b.example", **{"/": redirect("https://a.example/")})
        result = await h.fetch("https://a.example/")
        assert result.failure_code == "redirect_limit"
        assert len(h.web.page_paths("a.example")) + len(h.web.page_paths("b.example")) == 4

    @pytest.mark.parametrize(
        "target",
        [
            "https://kv-prod.vault.azure.net/secrets/x",
            "https://db.database.azure.com/",
            "https://ib-stg-api.scm.azurewebsites.net/api/zip",
            "https://ib-stg-api.azurewebsites.net/api/v1/admin",
        ],
    )
    async def test_ssrf15_redirect_to_a_denied_suffix_is_refused(
        self, h: Harness, target: str
    ) -> None:
        h.web.site("example.com", **{"/go": redirect(target)})
        result = await h.fetch("https://example.com/go")
        assert result.status == STATUS_REFUSED and result.policy_decision == "denied"
        host = target.split("/")[2]
        assert host not in h.web.hosts() and host not in h.dns.calls

    async def test_the_domain_denylist_refuses_without_resolving(self, h: Harness) -> None:
        result = await h.fetch("https://web.archive.org/web/2026/https://example.com/")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "denylisted_domain"
        assert "web.archive.org" not in h.dns.calls and h.web.requests == []
        rows = await h.rows()
        assert rows[0].redirect_chain_json[-1]["meta"]["denylist_version"] == DENYLIST_VERSION

    async def test_a_redirect_to_a_denylisted_domain_is_refused(self, h: Harness) -> None:
        h.web.site("example.com", **{"/go": redirect("https://12ft.io/proxy?q=x")})
        result = await h.fetch("https://example.com/go")
        assert result.failure_code == "denylisted_domain"
        assert "12ft.io" not in h.web.hosts()

    def test_denylist_matches_subdomains_only_on_label_boundaries(self) -> None:
        assert denylisted("www.reddit.com") and denylisted("reddit.com")
        assert not denylisted("notreddit.com") and not denylisted("example.com")

    async def test_an_http_url_from_search_is_refused_not_upgraded(self, h: Harness) -> None:
        result = await h.fetch("http://example.com/")
        assert result.status == STATUS_REFUSED and result.failure_code == "blocked_scheme"
        assert h.web.requests == []

    async def test_a_dns_failure_is_coded_and_not_negative_cached(self, h: Harness) -> None:
        result = await h.fetch("https://nowhere.example/")
        assert result.failure_code == "dns_failure"
        again = await h.fetch("https://nowhere.example/")
        assert again.status != STATUS_NEGATIVE_CACHED


# --------------------------------------------------------------------------- #
# robots.txt (RFC 9309)
# --------------------------------------------------------------------------- #


class TestRobotsParser:
    def test_our_group_overrides_star(self) -> None:
        policy = robots.parse_robots(
            "User-agent: *\nDisallow: /\n\n"
            "User-agent: InvestingBuddy-Research-Bot\nDisallow: /private\n"
        )
        assert policy.names_us
        assert policy.decide("/public") == robots.ROBOTS_ALLOWED
        assert policy.decide("/private/x") == robots.ROBOTS_DISALLOWED

    def test_star_applies_when_no_group_names_us(self) -> None:
        policy = robots.parse_robots(
            "User-agent: OtherBot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp"
        )
        assert not policy.names_us
        assert policy.decide("/") == robots.ROBOTS_ALLOWED
        assert policy.decide("/tmp/a") == robots.ROBOTS_DISALLOWED

    def test_longest_match_wins_and_allow_wins_a_tie(self) -> None:
        policy = robots.parse_robots(
            "User-agent: *\nDisallow: /reports\nAllow: /reports/annual\nDisallow: /x/\nAllow: /x/\n"
        )
        assert policy.decide("/reports/annual/2025.pdf") == robots.ROBOTS_ALLOWED
        assert policy.decide("/reports/q1.pdf") == robots.ROBOTS_DISALLOWED
        assert policy.decide("/x/y") == robots.ROBOTS_ALLOWED

    def test_wildcards_and_end_anchor(self) -> None:
        policy = robots.parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /search*q=\n")
        assert policy.decide("/doc.pdf") == robots.ROBOTS_DISALLOWED
        assert policy.decide("/doc.pdf?x=1") == robots.ROBOTS_ALLOWED
        assert policy.decide("/search?q=abc") == robots.ROBOTS_DISALLOWED

    def test_percent_encoding_is_normalised(self) -> None:
        policy = robots.parse_robots("User-agent: *\nDisallow: /caf%C3%A9\n")
        assert policy.decide("/café/menu") == robots.ROBOTS_DISALLOWED
        assert robots.normalise_path("/%7Euser/%2f") == "/~user/%2F"

    def test_group_lines_merge_and_versioned_tokens_match(self) -> None:
        policy = robots.parse_robots(
            "User-agent: investingbuddy-research-bot/1.0\nUser-agent: other\nDisallow: /a\n"
            "Crawl-delay: 3\n\nUser-agent: InvestingBuddy-Research-Bot\nDisallow: /b\n"
        )
        assert policy.decide("/a") == robots.ROBOTS_DISALLOWED
        assert policy.decide("/b") == robots.ROBOTS_DISALLOWED
        assert policy.crawl_delay == 3.0

    def test_robots_txt_itself_is_always_allowed_and_empty_disallow_allows(self) -> None:
        policy = robots.parse_robots("User-agent: *\nDisallow: /\n")
        assert policy.decide("/robots.txt") == robots.ROBOTS_ALLOWED
        assert (
            robots.parse_robots("User-agent: *\nDisallow:\n").decide("/") == robots.ROBOTS_ALLOWED
        )

    @pytest.mark.parametrize(
        ("answer", "source"),
        [
            (robots.SmallFetch(404), robots.ROBOTS_NONE),
            (robots.SmallFetch(401), robots.ROBOTS_NONE),
            (robots.SmallFetch(403), robots.ROBOTS_NONE),
            (robots.SmallFetch(429), robots.ROBOTS_UNAVAILABLE),
            (robots.SmallFetch(500), robots.ROBOTS_UNAVAILABLE),
            (robots.SmallFetch(503), robots.ROBOTS_UNAVAILABLE),
            (robots.SmallFetch(None, failed=True), robots.ROBOTS_UNAVAILABLE),
        ],
    )
    def test_status_mapping(self, answer: robots.SmallFetch, source: str) -> None:
        assert robots.policy_from_answer(answer).decision_source == source


class TestRobotsThroughTheFetch:
    async def test_disallow_means_no_fetch(self, h: Harness) -> None:
        h.web.site(
            "example.com",
            **{
                "/robots.txt": respond(
                    body=b"User-agent: InvestingBuddy-Research-Bot\nDisallow: /private\n",
                    content_type="text/plain",
                ),
                "/private/doc": respond(),
            },
        )
        result = await h.fetch("https://example.com/private/doc")
        assert result.status == STATUS_NOT_RETRIEVABLE
        assert result.failure_code == "robots_disallowed"
        assert result.robots_decision == robots.ROBOTS_DISALLOWED
        assert h.web.page_paths("example.com") == []

    async def test_allowed_path_is_fetched_and_robots_is_cached(self, h: Harness) -> None:
        h.web.site(
            "example.com",
            **{
                "/robots.txt": respond(
                    body=b"User-agent: *\nDisallow: /private\n", content_type="text/plain"
                ),
                "/a": respond(),
                "/b": respond(),
            },
        )
        first = await h.fetch("https://example.com/a")
        second = await h.fetch("https://example.com/b")
        assert first.robots_decision == robots.ROBOTS_ALLOWED
        assert second.status == STATUS_FETCHED
        assert h.web.paths("example.com").count("/robots.txt") == 1

    async def test_4xx_robots_means_allowed(self, h: Harness) -> None:
        h.web.site("example.com", **{"/robots.txt": respond(403, b"no"), "/a": respond()})
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED
        assert result.robots_decision == robots.ROBOTS_NONE

    async def test_5xx_robots_fails_closed_after_one_retry(self, h: Harness) -> None:
        h.web.site("example.com", **{"/robots.txt": respond(503, b"down"), "/a": respond()})
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "robots_unavailable"
        assert h.web.paths("example.com").count("/robots.txt") == 2  # one retry
        assert h.web.page_paths("example.com") == []
        # Cached for the run: the next URL on the origin does not ask again.
        again = await h.fetch("https://example.com/b")
        assert again.failure_code == "robots_unavailable"
        assert h.web.paths("example.com").count("/robots.txt") == 2

    async def test_unreachable_robots_fails_closed(self, h: Harness) -> None:
        h.web.site("example.com", **{"/robots.txt": connect_error, "/a": respond()})
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "robots_unavailable"
        assert h.web.page_paths("example.com") == []

    async def test_robots_is_fetched_through_the_same_guard(self, h: Harness) -> None:
        h.web.site(
            "example.com",
            **{
                "/robots.txt": redirect("https://169.254.169.254/latest/meta-data/"),
                "/a": respond(),
            },
        )
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "robots_unavailable"
        assert "169.254.169.254" not in h.web.ips() + h.web.hosts()

    async def test_crawl_delay_is_honoured_and_capped_at_ten_seconds(self, h: Harness) -> None:
        h.web.site(
            "example.com",
            **{
                "/robots.txt": respond(
                    body=b"User-agent: *\nCrawl-delay: 5\n", content_type="text/plain"
                ),
                "/a": respond(),
                "/b": respond(),
            },
        )
        h.web.site(
            "news.example.org",
            **{
                "/robots.txt": respond(
                    body=b"User-agent: *\nCrawl-delay: 60\n", content_type="text/plain"
                ),
                "/n": respond(),
            },
        )
        await h.fetch("https://example.com/a")
        await h.fetch("https://example.com/b")
        await h.fetch("https://news.example.org/n")
        limiter = h.runtime.limiter
        assert limiter.interval_for("example.com") == 5.0
        assert limiter.interval_for("example.org") == 10.0
        starts = [t for d, t in limiter.starts if d == "example.com"]
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        # robots, tdmrep, /a at 1 s pacing, then /b after the 5 s crawl delay.
        assert gaps[-1] >= 5.0


# --------------------------------------------------------------------------- #
# TDM reservation
# --------------------------------------------------------------------------- #


class TestTdm:
    async def test_tdm_reservation_header(self, h: Harness) -> None:
        h.web.site("example.com", **{"/a": respond(tdm_reservation="1")})
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_NOT_RETRIEVABLE
        assert result.failure_code == "tdm_reserved"
        assert result.tdm_decision == robots.TDM_RESERVED
        assert result.content is None and not result.ok
        row = (await h.rows())[0]
        assert row.tdm_decision == "tdm_reserved"
        assert row.redirect_chain_json[-1]["meta"]["tdm_signals"] == ["tdm_reservation_header"]

    async def test_well_known_tdmrep_reserves_a_path_without_fetching_it(self, h: Harness) -> None:
        tdmrep = json.dumps(
            [
                {"location": "/reports/*", "tdm-reservation": 1, "tdm-policy": "https://x/p"},
                {"location": "/*", "tdm-reservation": 0},
            ]
        ).encode()
        h.web.site(
            "example.com",
            **{
                robots.TDMREP_PATH: respond(body=tdmrep, content_type="application/json"),
                "/reports/annual": respond(),
                "/about": respond(),
            },
        )
        reserved = await h.fetch("https://example.com/reports/annual")
        free = await h.fetch("https://example.com/about")
        assert reserved.failure_code == "tdm_reserved"
        assert "/reports/annual" not in h.web.paths("example.com")
        assert free.status == STATUS_FETCHED
        assert free.tdm_decision == robots.TDM_NOT_RESERVED
        assert h.web.paths("example.com").count(robots.TDMREP_PATH) == 1

    @pytest.mark.parametrize(
        ("headers", "body", "signal"),
        [
            (
                {},
                b'<html><head><meta name="robots" content="noai, noimageai"></head>' + HTML_PAGE,
                "noai",
            ),
            (
                {},
                b'<html><head><meta name="tdm-reservation" content="1"></head>' + HTML_PAGE,
                "tdm_reservation_meta",
            ),
            ({"x_robots_tag": "noai"}, HTML_PAGE, "noai"),
            ({"content_usage": "train-ai=n, search=y"}, HTML_PAGE, "content_usage"),
        ],
    )
    async def test_meta_and_header_signals(
        self, h: Harness, headers: dict[str, str], body: bytes, signal: str
    ) -> None:
        h.web.site("example.com", **{"/a": respond(body=body, **headers)})
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "tdm_reserved"
        assert signal in result.tdm_signals

    def test_content_usage_y_does_not_reserve(self) -> None:
        assert not robots.content_usage_reserves("train-ai=y, search=y")
        assert robots.content_usage_reserves('tdm="n"')

    async def test_unreachable_tdmrep_is_recorded_unknown_and_the_page_fetched(
        self, h: Harness
    ) -> None:
        h.web.site("example.com", **{robots.TDMREP_PATH: respond(500, b"x"), "/a": respond()})
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED
        assert result.tdm_decision == robots.TDM_UNKNOWN


# --------------------------------------------------------------------------- #
# Pacing and concurrency (fake clock)
# --------------------------------------------------------------------------- #


class TestPacing:
    async def test_one_request_per_second_per_registrable_domain(self, h: Harness) -> None:
        for path in ("/1", "/2", "/3"):
            h.web.route("example.com", path, respond())
        h.web.route("www.example.com", "/4", respond())
        for path in ("/1", "/2", "/3"):
            await h.fetch(f"https://example.com{path}")
        await h.fetch("https://www.example.com/4")
        starts = [t for d, t in h.runtime.limiter.starts if d == "example.com"]
        # robots + tdmrep for two origins, and four pages: all one domain, all paced.
        assert len(starts) == 8
        assert all(b - a >= 1.0 for a, b in zip(starts, starts[1:], strict=False))

    async def test_other_domains_are_not_delayed(self, h: Harness) -> None:
        h.web.route("example.com", "/a", respond())
        h.web.route("news.example.org", "/b", respond())
        await h.fetch("https://example.com/a")
        before = h.clock.now
        h.clock.sleeps.clear()
        await h.fetch("https://news.example.org/b")
        # Only the new domain's own robots → tdmrep → page pacing (2 × 1 s) applies.
        assert h.clock.now - before <= 2.0 + 1e-9

    async def test_per_host_and_global_concurrency(self) -> None:
        limiter = OpenWebLimiter(min_interval_seconds=0.001)
        active: dict[str, int] = {}
        peak: dict[str, int] = {}
        peak_total = 0
        release = asyncio.Event()

        async def worker(domain: str) -> None:
            nonlocal peak_total
            async with limiter.slot(domain):
                active[domain] = active.get(domain, 0) + 1
                peak[domain] = max(peak.get(domain, 0), active[domain])
                peak_total = max(peak_total, sum(active.values()))
                await release.wait()
                active[domain] -= 1

        tasks = [asyncio.create_task(worker("one.example")) for _ in range(5)]
        tasks += [asyncio.create_task(worker(f"d{i}.example")) for i in range(6)]
        for _ in range(50):
            await asyncio.sleep(0.001)
        release.set()
        await asyncio.gather(*tasks)
        assert peak["one.example"] <= 2
        assert peak_total <= 4

    def test_the_sliding_window_admits_at_exactly_the_interval(self) -> None:
        window = SlidingWindowLimiter(1, 1.0)
        assert window.allow(10.0)
        assert not window.allow(10.5) and window.wait_seconds(10.5) == pytest.approx(0.5)
        assert window.allow(11.0)


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #


class TestRetries:
    async def test_429_with_a_short_retry_after_is_retried_once(self, h: Harness) -> None:
        h.web.route("example.com", "/a", [respond(429, b"slow", retry_after="2"), respond()])
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED and result.retries == 1
        assert 2.0 in h.clock.sleeps
        assert h.web.page_paths("example.com") == ["/a", "/a"]
        rows = await h.rows()
        assert [r.status for r in rows] == [STATUS_RETRIED, STATUS_FETCHED]
        assert rows[0].failure_code == "http_429" and rows[0].http_status == 429
        assert h.budget.fetches == 1  # a retried fetch counts once

    async def test_429_with_a_long_or_absent_retry_after_is_not_retried(self, h: Harness) -> None:
        h.web.route("example.com", "/long", respond(429, b"x", retry_after="120"))
        h.web.route("example.com", "/none", respond(429, b"x"))
        long = await h.fetch("https://example.com/long")
        none = await h.fetch("https://example.com/none")
        assert long.failure_code == none.failure_code == "http_429"
        assert h.web.page_paths("example.com") == ["/long", "/none"]

    async def test_5xx_is_retried_once_with_deterministic_jitter(self, h: Harness) -> None:
        h.web.route("example.com", "/a", [respond(503, b"x"), respond(503, b"x")])
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FAILED and result.failure_code == "http_503"
        assert h.web.page_paths("example.com") == ["/a", "/a"]
        expected = 1.0 + random.Random(7).uniform(0, 0.5)  # the runtime's seeded RNG
        assert any(s == pytest.approx(expected) for s in h.clock.sleeps)

    async def test_403_is_never_retried(self, h: Harness) -> None:
        h.web.route("example.com", "/a", respond(403, b"forbidden"))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "http_403" and result.retries == 0
        assert h.web.page_paths("example.com") == ["/a"]

    async def test_a_connect_error_is_retried_once(self, h: Harness) -> None:
        h.web.route("example.com", "/a", [connect_error, respond()])
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED and result.retries == 1
        assert [r.failure_code for r in await h.rows()] == ["connect_error", None]

    async def test_a_cloudflare_challenge_503_is_captcha_and_not_retried(self, h: Harness) -> None:
        page = b"<html><title>Just a moment...</title><div class='cf-turnstile'></div></html>"
        h.web.route("example.com", "/a", respond(503, page))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "captcha"
        assert result.status == STATUS_NOT_RETRIEVABLE
        assert h.web.page_paths("example.com") == ["/a"]

    def test_retry_after_parses_seconds_and_http_dates(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
        assert parse_retry_after("7") == 7.0
        assert parse_retry_after("Wed, 30 Sep 2026 10:00:20 GMT", now=now) == 20.0
        assert parse_retry_after("soon") is None


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #


class TestCanonical:
    def test_tracking_parameters_are_removed(self) -> None:
        url = (
            "https://Example.com:443/a?utm_source=x&UTM_Medium=y&fbclid=1&gclid=2&mc_cid=3"
            "&mc_eid=4&_hsenc=5&_hsmi=6&ref=7&ref_src=8&cmpid=9&ocid=10&igshid=11&id=42#frag"
        )
        # Plain ``ref`` is a document reference on many sites and is KEPT (review, low).
        assert canonical_url(url) == "https://example.com/a?ref=7&id=42"

    def test_same_domain_rel_canonical_is_honoured(self) -> None:
        decision = choose_canonical("https://www.example.com/a?x=1", "https://example.com/story")
        assert decision.honoured and decision.canonical_url == "https://example.com/story"

    def test_cross_domain_rel_canonical_is_recorded_not_honoured(self) -> None:
        decision = choose_canonical("https://example.com/a", "https://other.example.net/a")
        assert not decision.honoured and decision.reason == "cross_domain"
        assert decision.declared == "https://other.example.net/a"
        assert decision.canonical_url == "https://example.com/a"

    def test_link_header_canonical(self) -> None:
        header = '<https://example.com/doc.pdf>; rel="canonical", <https://x/y>; rel="next"'
        assert rel_canonical_from_header(header) == "https://example.com/doc.pdf"

    async def test_rel_canonical_never_changes_what_was_fetched(self, h: Harness) -> None:
        body = b'<html><head><link rel="canonical" href="/story"></head>' + HTML_PAGE
        h.web.route("www.example.com", "/amp/story", respond(body=body))
        result = await h.fetch("https://www.example.com/amp/story?utm_source=feed")
        assert result.canonical_url == "https://www.example.com/story"
        assert result.rel_canonical_honoured
        assert result.final_url == "https://www.example.com/amp/story?utm_source=feed"
        assert h.web.page_paths("www.example.com") == ["/amp/story"]


# --------------------------------------------------------------------------- #
# Content: charset, sniffing, caps, bombs
# --------------------------------------------------------------------------- #


LATIN1_TEXT = "Café résumé — naïve façade"
WIN1252_TEXT = "“Quoted” price €12 – up"
SJIS_TEXT = "東京証券取引所に上場している電力機器メーカーの年次報告書です。" * 8


class TestCharset:
    def test_latin1_header_label_decodes_as_windows_1252(self) -> None:
        body = f"<html><body><p>{LATIN1_TEXT.replace('—', '-')}</p></body></html>".encode("latin-1")
        codec, source = content.detect_charset(
            body, content_type="text/html; charset=ISO-8859-1", content_class="html"
        )
        assert (codec, source) == ("cp1252", "header")
        assert "Café résumé" in content.decode_text(body, codec)

    def test_windows_1252_from_meta(self) -> None:
        body = (
            b'<html><head><meta charset="windows-1252"></head><body>'
            + WIN1252_TEXT.encode("cp1252")
            + b"</body></html>"
        )
        codec, source = content.detect_charset(body, content_type="text/html", content_class="html")
        assert (codec, source) == ("cp1252", "meta")
        assert WIN1252_TEXT in content.decode_text(body, codec)

    def test_shift_jis_is_detected_without_a_label(self) -> None:
        body = ("<html><body><p>" + SJIS_TEXT + "</p></body></html>").encode("shift_jis")
        codec, source = content.detect_charset(body, content_type="text/html", content_class="html")
        assert source == "detected"
        assert SJIS_TEXT[:10] in content.decode_text(body, codec)

    def test_a_bom_wins_over_the_header(self) -> None:
        body = codecs.BOM_UTF8 + "naïve".encode()
        codec, source = content.detect_charset(
            body, content_type="text/plain; charset=iso-8859-1", content_class="text"
        )
        assert (codec, source) == ("utf-8", "bom")
        assert content.decode_text(body, codec) == "naïve"

    async def test_the_fetch_reports_decoded_text(self, h: Harness) -> None:
        body = ("<html><body><p>" + SJIS_TEXT + "</p></body></html>").encode("shift_jis")
        h.web.route("example.com", "/jp", respond(body=body, content_type="text/html"))
        result = await h.fetch("https://example.com/jp")
        assert result.charset_source == "detected"
        assert SJIS_TEXT[:10] in (result.text() or "")


class TestSniffing:
    async def test_file01_pdf_url_serving_html_is_routed_as_html_and_flagged(
        self, h: Harness
    ) -> None:
        h.web.route("example.com", "/report.pdf", respond())
        result = await h.fetch("https://example.com/report.pdf")
        assert result.content_class == "html" and result.mime_sniffed == "text/html"
        assert result.mime_mismatch

    async def test_file01_html_declared_as_pdf_is_flagged(self, h: Harness) -> None:
        h.web.route("example.com", "/x", respond(content_type="application/pdf"))
        result = await h.fetch("https://example.com/x")
        assert result.mime_served == "application/pdf" and result.content_class == "html"
        assert result.mime_mismatch
        row = (await h.rows())[0]
        assert row.mime_served == "application/pdf" and row.mime_sniffed == "text/html"
        assert row.redirect_chain_json[-1]["meta"]["mime_mismatch"] is True

    async def test_a_pdf_served_as_octet_stream_is_a_pdf(self, h: Harness) -> None:
        pdf = b"%PDF-1.7\n" + b"0" * 5000
        h.web.route("example.com", "/d", respond(body=pdf, content_type="application/octet-stream"))
        result = await h.fetch("https://example.com/d")
        assert result.content_class == "pdf" and not result.mime_mismatch
        assert result.status == STATUS_FETCHED and h.budget.pdfs == 1

    async def test_office_containers_are_refused_after_the_sniff_prefix(self, h: Harness) -> None:
        docx = b"PK\x03\x04" + b"\x00" * 500_000
        h.web.route(
            "example.com",
            "/f.docx",
            respond(
                body=docx,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        result = await h.fetch("https://example.com/f.docx")
        assert result.status == STATUS_REFUSED and result.failure_code == "unsupported_type"
        assert result.bytes <= 1024 and result.content is None

    @pytest.mark.parametrize(
        ("prefix", "cls"),
        [
            (b"%PDF-1.4", "pdf"),
            (b"  \n<!DOCTYPE html><html>", "html"),
            (codecs.BOM_UTF8 + b"<html>", "html"),
            ("<html>".encode("utf-16"), "html"),
            (b'<?xml version="1.0"?><svg xmlns="x">', "binary"),
            (b"PK\x03\x04", "office"),
            (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "office"),
            (b"\x89PNG\r\n\x1a\n", "binary"),
            (b'{"a": 1}', "text"),
            (b"plain words", "text"),
            (b"<abbr>not html sniff</abbr>", "text"),
        ],
    )
    def test_sniff_table(self, prefix: bytes, cls: str) -> None:
        assert content.sniff(prefix).content_class == cls


class TestCaps:
    async def test_file02_oversized_html_is_truncated_and_never_complete(self, h: Harness) -> None:
        body = b"<html><body>" + b"<p>word</p>" * 400_000  # ~4.4 MB
        h.web.route("example.com", "/big", respond(body=body))
        result = await h.fetch("https://example.com/big")
        assert result.status == STATUS_PARTIAL
        assert result.truncated and not result.complete and result.ok
        assert result.bytes == content.CLASS_BYTE_CAPS["html"]
        row = (await h.rows())[0]
        assert row.truncated is True and row.status == "fetched_partial"

    async def test_file02_text_class_cap_is_two_megabytes(self, h: Harness) -> None:
        body = b"plain text line\n" * 200_000  # 3.2 MB
        h.web.route("example.com", "/t.txt", respond(body=body, content_type="text/plain"))
        result = await h.fetch("https://example.com/t.txt")
        assert result.truncated and result.bytes == content.CLASS_BYTE_CAPS["text"]

    async def test_file03_gzip_bomb_is_stopped_by_the_ratio_cap(self, h: Harness) -> None:
        bomb = gzip.compress(b"<html><body>" + b" " * 60_000_000, compresslevel=9)
        h.web.route(
            "example.com",
            "/bomb",
            streamed({"content-type": "text/html", "content-encoding": "gzip"}, bomb),
        )
        result = await h.fetch("https://example.com/bomb")
        assert result.status == STATUS_FAILED
        assert result.failure_code == "decompression_bomb"
        assert result.content is None

    async def test_the_sniff_hook_narrows_but_never_widens(self) -> None:
        body = b"<html>" + b"x" * 10_000

        class _Resp:
            headers: dict[str, str] = {}

            async def aiter_bytes(self):  # noqa: ANN202
                for i in range(0, len(body), 700):
                    yield body[i : i + 700]

        read = await read_bounded_body(_Resp(), max_bytes=5000, sniff_cap=lambda _p: 10**9)
        assert read.truncated and len(read.content) == 5000
        read = await read_bounded_body(_Resp(), max_bytes=10**6, sniff_cap=lambda _p: 2000)
        assert read.truncated and len(read.content) == 2000
        read = await read_bounded_body(_Resp(), max_bytes=10**6)
        assert not read.truncated and read.content == body

    async def test_js_shell_is_flagged_js_required(self, h: Harness) -> None:
        shell = (
            b'<!doctype html><html><head><script id="__NEXT_DATA__">'
            + b"x" * 30_000
            + b'</script></head><body><div id="__next"></div></body></html>'
        )
        h.web.route("example.com", "/spa", respond(body=shell))
        result = await h.fetch("https://example.com/spa")
        assert result.js_required and result.status == STATUS_FETCHED
        assert not content.js_required(len(HTML_PAGE), HTML_PAGE.decode())


# --------------------------------------------------------------------------- #
# Access restrictions (never bypassed)
# --------------------------------------------------------------------------- #


ARTICLE = "<p>" + "The plant doubled output after the new line opened. " * 100 + "</p>"


class TestAccess:
    async def test_402_is_not_retrievable(self, h: Harness) -> None:
        h.web.route("example.com", "/a", respond(402, b"pay"))
        result = await h.fetch("https://example.com/a")
        assert (result.status, result.failure_code) == (STATUS_NOT_RETRIEVABLE, "http_402")

    @pytest.mark.parametrize("value", ["false", '"False"'])
    async def test_json_ld_paywall(self, h: Harness, value: str) -> None:
        body = (
            '<html><head><script type="application/ld+json">{"@type":"NewsArticle",'
            f'"hasPart":{{"@type":"WebPageElement","isAccessibleForFree":{value}}}}}'
            f"</script></head><body>{ARTICLE}</body></html>"
        ).encode()
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "paywall_jsonld" and result.content is None

    async def test_login_wall_by_redirect(self, h: Harness) -> None:
        h.web.route("example.com", "/a", redirect("/account/login?next=/a"))
        h.web.route("example.com", "/account/login", respond())
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "login_wall"

    async def test_login_wall_by_password_form(self, h: Harness) -> None:
        body = b'<html><body><h1>Sign in</h1><form><input type="password" name="p"></form></body></html>'
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "login_wall"

    async def test_captcha_page(self, h: Harness) -> None:
        body = b'<html><body><div class="g-recaptcha" data-sitekey="x"></div></body></html>'
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "captcha"

    async def test_a_real_article_with_a_contact_form_captcha_is_not_a_wall(
        self, h: Harness
    ) -> None:
        body = (
            f"<html><body>{ARTICLE}<form><div class='g-recaptcha'></div>"
            "<input type='password'></form></body></html>"
        ).encode()
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED

    async def test_consent_redirect(self, h: Harness) -> None:
        h.web.route("example.com", "/a", redirect("https://www.example.com/consent?x=1"))
        h.web.route("www.example.com", "/consent", respond())
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "consent_wall"

    async def test_the_client_never_sends_a_cookie_even_when_offered_one(self, h: Harness) -> None:
        def set_cookie(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "/b", "set-cookie": "session=abc; Path=/"}
            )

        h.web.route("example.com", "/a", set_cookie)
        h.web.route("example.com", "/b", respond())
        await h.fetch("https://example.com/a")
        assert all("cookie" not in r.headers for r in h.web.requests)

    def test_status_classifier(self) -> None:
        assert access.classify_status(401).reason == "http_401"
        assert access.classify_status(404).retrievable


# --------------------------------------------------------------------------- #
# Negative cache and the budget
# --------------------------------------------------------------------------- #


class TestNegativeCache:
    async def test_a_404_is_not_asked_again_even_with_tracking_params(self, h: Harness) -> None:
        h.web.route("example.com", "/gone", respond(404, b"gone"))
        first = await h.fetch("https://example.com/gone")
        second = await h.fetch("https://example.com/gone?utm_source=newsletter")
        assert first.failure_code == "http_404"
        assert second.status == STATUS_NEGATIVE_CACHED and second.failure_code == "http_404"
        assert h.web.page_paths("example.com") == ["/gone"]
        rows = await h.rows()
        assert (
            rows[-1].status == "negative_cached" and rows[-1].policy_decision == "negative_cached"
        )

    async def test_a_paywall_is_negative_cached(self, h: Harness) -> None:
        h.web.route("example.com", "/p", respond(402, b"pay"))
        await h.fetch("https://example.com/p")
        again = await h.fetch("https://example.com/p")
        assert again.status == STATUS_NEGATIVE_CACHED

    def test_24_hours_then_7_days_on_a_repeat(self, clock: FakeClock) -> None:
        cache = NegativeCache(clock=clock)
        assert cache.record("k", "http_403") == FIRST_TTL_SECONDS
        assert cache.get("k") == "http_403"
        clock.now += FIRST_TTL_SECONDS + 1
        assert cache.get("k") is None
        assert cache.record("k", "http_403") == REPEAT_TTL_SECONDS
        clock.now += FIRST_TTL_SECONDS + 1
        assert cache.get("k") == "http_403"
        clock.now += REPEAT_TTL_SECONDS
        assert cache.get("k") is None


class TestBudget:
    async def test_the_per_domain_cap_refuses_without_network(
        self, h: Harness, clock: FakeClock
    ) -> None:
        budget = _budget(clock, "company_quick")  # 4 per domain
        for i in range(5):
            h.web.route("example.com", f"/{i}", respond())
        results = [await h.fetch(f"https://example.com/{i}", budget=budget) for i in range(5)]
        assert [r.status for r in results[:4]] == [STATUS_FETCHED] * 4
        assert results[4].failure_code == "budget:max_per_domain"
        assert "/4" not in h.web.paths("example.com")
        assert budget.fetches == 4 and budget.bytes_downloaded >= 4 * len(HTML_PAGE)

    async def test_the_run_fetch_cap(self, h: Harness, clock: FakeClock) -> None:
        budget = _budget(clock, "company_quick")
        budget.fetches = budget.limits.max_fetches
        result = await h.fetch("https://example.com/", budget=budget)
        assert result.failure_code == "budget:max_fetches" and h.web.requests == []


# --------------------------------------------------------------------------- #
# User-supplied URLs (spec §21)
# --------------------------------------------------------------------------- #


class TestUserUrls:
    def test_urls_are_extracted_and_deduplicated_by_canonical_form(self) -> None:
        text = (
            "Transformer makers. Also use https://example.com/r?utm_source=x and "
            "https://example.com/r, http://news.example.org/a, www.example.com/w."
        )
        assert extract_user_urls(text) == [
            "https://example.com/r?utm_source=x",
            "http://news.example.org/a",
            "www.example.com/w",
        ]

    async def test_the_user_path_refuses_internal_targets_and_upgrades_http(
        self, h: Harness
    ) -> None:
        h.dns.table["intranet.example"] = [PRIVATE]
        h.web.route("news.example.org", "/a", respond())
        text = (
            "Thesis: grid equipment. Sources: https://127.0.0.1/admin "
            "https://169.254.169.254/latest/meta-data/ https://intranet.example/x "
            "http://news.example.org/a ftp://example.com/f"
        )
        results = await fetch_user_urls(
            h.session,
            text,
            context=WebFetchContext(research_job_id=h.job_id),
            budget=h.budget,
            cfg=h.cfg,
            resolver=h.dns,
            runtime=h.runtime,
        )
        by_code = [(r.status, r.failure_code) for r in results]
        assert by_code[0] == (STATUS_REFUSED, "blocked_host")
        assert by_code[1] == (STATUS_REFUSED, "blocked_host")
        assert by_code[2] == (STATUS_REFUSED, "blocked_private_ip")
        assert by_code[3] == (STATUS_FETCHED, None) and results[3].upgraded_from_http
        assert by_code[4] == (STATUS_REFUSED, "blocked_scheme")
        assert set(h.web.hosts()) == {"news.example.org"}
        assert all(r.url.scheme == "https" for r in h.web.requests)
        rows = await h.rows()
        assert {r.origin for r in rows} == {"user"} and len(rows) == 5
        upgraded = [r for r in rows if r.status == "fetched"][0]
        assert upgraded.redirect_chain_json[-1]["meta"]["upgraded_from_http"] is True
        assert upgraded.requested_url.startswith("http://")

    async def test_the_user_path_is_gated_by_the_same_flag(self, h: Harness) -> None:
        results = await fetch_user_urls(
            h.session,
            "see https://example.com/x",
            context=WebFetchContext(),
            budget=h.budget,
            cfg=_cfg(v3_web_fetch_enabled=False),
            resolver=h.dns,
            runtime=h.runtime,
        )
        assert [r.failure_code for r in results] == ["web_fetch_disabled"]
        assert h.web.requests == [] and h.dns.calls == []


# --------------------------------------------------------------------------- #
# Metrics through the W1 audit read model (spec §22.1)
# --------------------------------------------------------------------------- #


class TestMetrics:
    async def test_the_audit_exposes_fetch_metrics(self, h: Harness) -> None:
        shell = (
            b'<html><body><div id="root"></div><script>'
            + b"x" * 30_000
            + b"</script></body></html>"
        )
        h.web.route("example.com", "/ok", respond())
        h.web.route("example.com", "/spa", respond(body=shell))
        h.web.route("example.com", "/forbidden", respond(403, b"no"))
        h.web.route("example.com", "/pay", respond(402, b"no"))
        h.web.route("example.com", "/retry", [respond(503, b"x"), respond()])
        h.web.route("example.com", "/r", redirect("/ok"))
        h.web.route("example.com", "/tdm", respond(tdm_reservation="1"))
        await h.fetch("https://example.com/ok")
        await h.fetch("https://example.com/spa")
        await h.fetch("https://example.com/forbidden")
        await h.fetch("https://example.com/pay")
        await h.fetch("https://example.com/retry")
        await h.fetch("https://example.com/r")
        await h.fetch("https://example.com/tdm")
        await h.fetch("https://web.archive.org/x")
        await h.fetch("https://example.com/forbidden")  # a negative-cache hit
        spent = _budget(h.clock)
        spent.fetches = spent.limits.max_fetches
        await h.fetch("https://example.com/ok", budget=spent)  # a budget refusal
        await h.session.flush()

        audit = await web_research_audit(h.session, research_job_id=h.job_id)
        m = audit.totals.fetch_metrics
        assert m.attempts == 8
        assert m.fetched == 4  # ok, spa, retry, redirect → ok
        assert m.success_rate == 0.5
        assert m.http_403 == 1 and m.paywall == 1 and m.tdm_reserved == 1
        assert m.policy_denied == 1 and m.retries == 1 and m.redirects == 1
        assert m.js_required == 1
        assert m.bytes > 0
        # Rows: 8 logical + 1 retried physical attempt + robots.txt + tdmrep.json.
        assert audit.totals.fetch_attempts == 13
        assert m.policy_file_requests == 2
        # Neither a cache hit nor a budget refusal is an attempt (review, low).
        assert m.negative_cached == 1 and m.budget_refused == 1
        pages = [f for f in audit.fetch_attempts if f.origin == "search"]
        assert pages[0].canonical_url == "https://example.com/ok"


# --------------------------------------------------------------------------- #
# Hostile markup: every scanner is linear (no <tag[^>]*> backtracking)
# --------------------------------------------------------------------------- #


class TestHostileMarkup:
    def test_tag_scanner_finds_real_tags_only(self) -> None:
        html = '<metadata><meta name="a"><META\nname="b"/><meta'
        tags = list(content.iter_start_tags(html, "meta"))
        assert tags == ['<meta name="a">', '<META\nname="b"/>']

    def test_visible_text_skips_scripts_comments_and_head(self) -> None:
        html = (
            "<html><head><title>T</title></head><body><!-- hidden --><p>Hello  world</p>"
            "<script>var x = '<p>no</p>';</script><style>p{}</style>a < b</body></html>"
        )
        assert content.visible_text_chars(html) == len("Hello world a < b")

    @pytest.mark.parametrize(
        "body",
        [
            b"<script " * 370_000,
            b"<meta " * 500_000,
            b'<script type="application/ld+json">' * 85_000,
            b"<link rel=canonical href=" * 110_000,
            b'g-recaptcha id="root" ' + b"<div>" * 590_000,
            b"<" * 3_000_000,
        ],
    )
    def test_three_megabytes_of_hostile_markup_is_analysed_in_bounded_time(
        self, body: bytes
    ) -> None:
        import time

        from app.services.web_research.fetch import _analyse

        started = time.perf_counter()
        _analyse(
            body,
            content_class="html",
            content_type="text/html",
            requested_url="https://example.com/a",
            final_url="https://example.com/a",
        )
        # Linear scans take well under 3 s here; a quadratic regex took minutes.
        assert time.perf_counter() - started < 20.0


# --------------------------------------------------------------------------- #
# Review round 1 — each blocking/high/medium finding, mutation-sensitive
# --------------------------------------------------------------------------- #

ARTICLE_900 = (
    "<article><h1>Plant expansion</h1>"
    + "<p>The company doubled transformer output after its new line opened. </p>" * 13
    + "</article>"
)


class _SlowFlushSession:
    """Delegates to a real session; ``flush`` first sleeps (real time)."""

    def __init__(self, inner: Any, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    def add(self, obj: Any) -> None:
        self._inner.add(obj)

    async def flush(self) -> None:
        await asyncio.sleep(self._delay)
        await self._inner.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestReviewBlocking:
    async def test_b1_a_deadline_during_the_flush_does_not_refinish(self, h: Harness) -> None:
        h.web.route("example.com", "/warm", respond())
        h.web.route("example.com", "/a", respond())
        await h.fetch("https://example.com/warm")  # warm DNS/PSL/robots so /a is fast
        slow = _SlowFlushSession(h.session, delay=2.5)
        result = await open_web_fetch(
            slow,
            "https://example.com/a",
            context=WebFetchContext(research_job_id=h.job_id),
            budget=h.budget,
            origin="search",
            cfg=_cfg(source_document_total_deadline_seconds=1.5),
            resolver=h.dns,
            runtime=h.runtime,
        )
        # The walk finished inside the deadline; the slow flush is outside it, so the
        # result is not rewritten as a timeout and exactly one page row exists.
        assert result.status == STATUS_FETCHED
        rows = await h.rows()
        assert [r.status for r in rows] == ["fetched", "fetched"]  # /warm, then /a once

    async def test_b2_a_negative_cache_hit_does_not_add_a_strike(self, h: Harness) -> None:
        h.web.route("example.com", "/gone", respond(404, b"gone"))
        await h.fetch("https://example.com/gone")
        for _ in range(3):
            hit = await h.fetch("https://example.com/gone")
            assert hit.status == STATUS_NEGATIVE_CACHED
        assert h.runtime.negative.strikes("https://example.com/gone") == 1


class TestReviewFalseWalls:
    async def test_h1_cloudflare_beacon_and_recaptcha_v3_are_not_a_captcha(
        self, h: Harness
    ) -> None:
        body = (
            "<html><head>"
            '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
            '<script src="https://www.google.com/recaptcha/api.js?render=6Lc_KEY"></script>'
            f"</head><body>{ARTICLE_900}</body></html>"
        ).encode()
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED and result.failure_code is None

    async def test_h1_a_nav_investor_login_box_is_not_a_login_wall(self, h: Harness) -> None:
        body = (
            "<html><body><header><nav><form action='/investor/login'>"
            "<input type='text' placeholder='Investor login'>"
            "<input type='password' name='pw'></form></nav></header>"
            f"{ARTICLE_900}</body></html>"
        ).encode()
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED

    async def test_h1_a_onetrust_banner_is_not_a_consent_wall(self, h: Harness) -> None:
        body = (
            "<html><body><div id='onetrust-banner-sdk'><p>We value your privacy. We and "
            "our partners use cookies.</p><button>Accept All Cookies</button></div>"
            f"{ARTICLE_900}</body></html>"
        ).encode()
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED

    async def test_h1_a_real_interstitial_is_still_a_captcha(self, h: Harness) -> None:
        body = (
            b"<html><head><title>Just a moment...</title></head><body>"
            b"<div id='cf-chl-widget'>Verify you are human by completing the action "
            b"below.</div></body></html>"
        )
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "captcha"

    async def test_h1_a_consent_only_page_is_still_a_consent_wall(self, h: Harness) -> None:
        body = (
            b"<html><body><h1>Before you continue</h1><p>We value your privacy.</p>"
            b"<button>Accept all cookies</button></body></html>"
        )
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "consent_wall"


class TestReviewRobotsDeadline:
    async def test_h2_our_own_run_deadline_never_caches_robots_unavailable(
        self, h: Harness
    ) -> None:
        async def slow_robots(_r: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1.5)
            return httpx.Response(404)

        h.web.route("example.com", "/robots.txt", slow_robots)
        h.web.route("example.com", "/a", respond())
        first = await h.fetch(
            "https://example.com/a", cfg=_cfg(source_document_total_deadline_seconds=0.3)
        )
        assert first.failure_code == "fetch_timeout"
        # The site was not slow by its own standard — our run ran out. Nothing cached:
        h.web.route("example.com", "/robots.txt", respond(404, b"none"))
        second = await h.fetch("https://example.com/a")
        assert second.status == STATUS_FETCHED
        assert second.robots_decision == robots.ROBOTS_NONE

    async def test_h2_policy_files_get_their_own_deadline_not_the_runs(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.web_research import fetch as fetch_module

        seen: list[tuple[str, float]] = []
        real_request = fetch_module._request

        async def recording(url: str, host: str, ip: str, **kw: Any) -> Any:
            seen.append((url, kw["deadline"] - asyncio.get_running_loop().time()))
            return await real_request(url, host, ip, **kw)

        monkeypatch.setattr(fetch_module, "_request", recording)
        h.web.route("example.com", "/a", respond())
        await h.fetch("https://example.com/a", cfg=_cfg(source_document_total_deadline_seconds=2.0))
        policy = [left for url, left in seen if url.endswith((".txt", ".json"))]
        page = [left for url, left in seen if url.endswith("/a")]
        assert policy and all(left > 10.0 for left in policy)  # its own 20 s budget
        assert page and all(left <= 2.0 for left in page)  # the run's deadline

    async def test_h2_a_cancelled_robots_fetch_is_never_cached(self) -> None:
        gate = robots.RobotsGate()
        calls = 0

        async def cancelled(_url: str, _max: int) -> robots.SmallFetch:
            nonlocal calls
            calls += 1
            raise TimeoutError  # what the run's own deadline looks like from here

        with pytest.raises(TimeoutError):
            await gate.check("https://example.com/a", cancelled)

        async def fine(_url: str, _max: int) -> robots.SmallFetch:
            return robots.SmallFetch(404)

        check = await gate.check("https://example.com/a", fine)
        assert check.decision == robots.ROBOTS_NONE and not check.from_cache

    async def test_s6_robots_redirects_follow_five_then_fail_closed(self, h: Harness) -> None:
        for i in range(4):
            h.web.route("example.com", f"/r{i}", redirect(f"/r{i + 1}"))
        h.web.route(
            "example.com",
            "/r4",
            respond(body=b"User-agent: *\nDisallow: /\n", content_type="text/plain"),
        )
        h.web.route("example.com", "/robots.txt", redirect("/r0"))
        h.web.route("example.com", "/a", respond())
        five = await h.fetch("https://example.com/a")
        # Five redirects (robots.txt → r0 → … → r4) are followed.
        assert five.failure_code == "robots_disallowed"

        h.web.route("news.example.org", "/robots.txt", redirect("/q0"))
        for i in range(7):
            h.web.route("news.example.org", f"/q{i}", redirect(f"/q{i + 1}"))
        h.web.route("news.example.org", "/n", respond())
        loop = await h.fetch("https://news.example.org/n")
        assert loop.failure_code == "robots_unavailable"
        assert "/n" not in h.web.paths("news.example.org")


class TestReviewBudget:
    async def test_m1_a_redirect_counts_against_the_target_domains_cap(
        self, h: Harness, clock: FakeClock
    ) -> None:
        budget = _budget(clock, "company_quick")  # 4 per domain
        for i in range(4):
            h.web.route("news.example.org", f"/{i}", respond())
        for i in range(4):
            assert (await h.fetch(f"https://news.example.org/{i}", budget=budget)).ok
        h.web.route("example.com", "/go", redirect("https://news.example.org/x"))
        h.web.route("news.example.org", "/x", respond())
        result = await h.fetch("https://example.com/go", budget=budget)
        assert result.failure_code == "budget:max_per_domain"
        assert "/x" not in h.web.paths("news.example.org")

    async def test_m2_a_sniffed_pdf_over_the_pdf_cap_is_not_read(
        self, h: Harness, clock: FakeClock
    ) -> None:
        budget = _budget(clock)
        budget.pdfs = budget.limits.max_pdfs
        pdf = b"%PDF-1.7\n" + b"0" * 200_000
        h.web.route("example.com", "/doc", respond(body=pdf, content_type="application/pdf"))
        result = await h.fetch("https://example.com/doc", budget=budget)  # no .pdf hint
        assert result.status == STATUS_REFUSED
        assert result.failure_code == "budget:max_pdfs"
        assert result.bytes <= 1024
        assert budget.pdfs == budget.limits.max_pdfs


class TestReviewSniffing:
    async def test_m3_html_served_as_html_with_an_unlisted_first_tag_is_html(
        self, h: Harness
    ) -> None:
        body = (
            b"<custom-banner>Site banner</custom-banner>"
            b'<meta name="tdm-reservation" content="1">' + HTML_PAGE
        )
        h.web.route("example.com", "/a", respond(body=body))
        result = await h.fetch("https://example.com/a")
        assert result.content_class == "html"
        assert result.failure_code == "tdm_reserved"

    def test_m3_the_served_type_never_makes_bytes_a_pdf(self) -> None:
        assert content.route(b"plain words", "application/pdf").content_class == "text"
        assert content.route(b"PK\x03\x04", "text/html").content_class == "office"


class TestReviewLimiter:
    async def test_m4_a_pacing_wait_holds_no_global_slot(self) -> None:
        now = [0.0]
        gate = asyncio.Event()

        async def blocked_sleep(seconds: float) -> None:
            await gate.wait()
            now[0] += seconds

        limiter = OpenWebLimiter(global_concurrency=1, clock=lambda: now[0], sleep=blocked_sleep)
        async with limiter.slot("a.example"):
            pass

        async def use(domain: str) -> bool:
            async with limiter.slot(domain):
                return True

        paced = asyncio.create_task(use("a.example"))  # must wait 1 s for its window
        await asyncio.sleep(0.01)
        assert await asyncio.wait_for(use("b.example"), timeout=2.0)
        gate.set()
        assert await paced

    def test_crawl_delay_never_narrows_across_origins(self) -> None:
        limiter = OpenWebLimiter()
        limiter.set_crawl_delay("example.com", 5)
        assert limiter.set_crawl_delay("example.com", 2) == 5.0

    async def test_an_in_flight_domain_is_never_evicted(self) -> None:
        now = [0.0]
        limiter = OpenWebLimiter(clock=lambda: now[0])
        async with limiter.slot("busy.example"):
            now[0] += 1000.0
            limiter._evict_idle()
            assert "busy.example" in limiter._domains


class TestReviewRowsAndCache:
    async def test_m5_robots_and_tdmrep_requests_write_rows(self, h: Harness) -> None:
        h.web.route("example.com", "/robots.txt", [respond(503, b"x"), respond(404, b"n")])
        h.web.route("example.com", "/a", respond())
        await h.fetch("https://example.com/a")
        rows = await h.all_rows()
        by_origin = [(r.origin, r.status, r.http_status) for r in rows]
        assert ("robots", "retried", 503) in by_origin
        assert ("robots", "failed", 404) in by_origin
        assert ("tdm", "failed", 404) in by_origin
        assert all(r.research_job_id == h.job_id for r in rows)
        assert h.budget.fetches == 1  # policy files are not page fetches

    async def test_m6_a_wall_is_cached_under_the_requested_url_not_its_canonical(
        self, h: Harness
    ) -> None:
        wall = (
            b'<html><head><link rel="canonical" href="/"></head><body><h1>Sign in</h1>'
            b'<form><input type="password" name="p"></form></body></html>'
        )
        h.web.route("example.com", "/members", respond(body=wall))
        h.web.route("example.com", "/", respond())
        first = await h.fetch("https://example.com/members")
        assert first.failure_code == "login_wall"
        home = await h.fetch("https://example.com/")
        assert home.status == STATUS_FETCHED
        again = await h.fetch("https://example.com/members")
        assert again.status == STATUS_NEGATIVE_CACHED


class TestReviewSecurity:
    def test_s1_a_backtracking_pattern_matches_in_bounded_time(self) -> None:
        import time

        policy = robots.parse_robots("User-agent: *\nDisallow: /*a*a*a*a*a*a*a*a*a*a*b\n")
        started = time.perf_counter()
        assert policy.decide("/" + "a" * 2000) == robots.ROBOTS_ALLOWED
        assert time.perf_counter() - started < 0.5

    def test_s1_a_huge_robots_file_is_capped_and_cheap(self) -> None:
        import time

        at_cap = "User-agent: *\n" + "".join(
            f"Disallow: /*x{i}*y*z$\n" for i in range(robots.MAX_ROBOTS_RULES)
        )
        started = time.perf_counter()
        policy = robots.parse_robots(at_cap)
        assert len(policy.rules) == robots.MAX_ROBOTS_RULES
        for _ in range(20):
            policy.decide("/" + "x1y" * 600)
        # Past the cap the file is not evaluated rule by rule: it fails closed (R2-2).
        huge = robots.parse_robots(at_cap + "".join(f"Disallow: /q{i}\n" for i in range(25_000)))
        assert huge.rules == (robots.RobotsRule(False, "/"),)
        # Backstop against a catastrophic regression (the original freeze was minutes);
        # generous because the full suite shares the machine.
        assert time.perf_counter() - started < 30.0

    def test_s1_tdmrep_uses_the_same_bounded_matcher(self) -> None:
        import time

        rules = robots.parse_tdmrep(
            json.dumps([{"location": "/*a*a*a*a*a*a*a*a*a*a*b", "tdm-reservation": 1}]).encode()
        )
        assert rules is not None
        started = time.perf_counter()
        assert not robots.tdmrep_reserves(rules, "/" + "a" * 2000)
        assert time.perf_counter() - started < 0.5

    @pytest.mark.parametrize(
        ("pattern", "path", "expected"),
        [
            ("/a*b", "/axxb", True),
            ("/a*b$", "/axxbc", False),
            ("/a*b$", "/axxb", True),
            ("/*.pdf$", "/x.pdf", True),
            ("/*.pdf$", "/.pdf.pdf", True),
            ("/a**b", "/ab", True),
            ("/a*", "/a", True),
            ("/a$", "/ab", False),
            ("/a*b*c", "/acb", False),
        ],
    )
    def test_s1_wildcard_semantics(self, pattern: str, path: str, expected: bool) -> None:
        assert robots.wildcard_match(pattern, path) is expected

    async def test_s1_a_hostile_robots_file_does_not_freeze_the_fetch(self, h: Harness) -> None:
        import time

        hostile = b"User-agent: *\n" + b"Disallow: /*a*a*a*a*a*a*a*a*a*a*a*b\n" * 1900
        h.web.route("example.com", "/robots.txt", respond(body=hostile, content_type="text/plain"))
        h.web.route("example.com", "/" + "a" * 1500, respond())
        started = time.perf_counter()
        result = await h.fetch("https://example.com/" + "a" * 1500)
        assert time.perf_counter() - started < 10.0
        assert result.robots_decision == robots.ROBOTS_ALLOWED

    async def test_s3a_charset_idna_cannot_select_a_python_codec(self, h: Harness) -> None:
        assert content.normalise_charset("idna") is None
        assert content.normalise_charset("rot13") is None
        assert content.decode_text("café".encode("latin-1"), "idna")  # never raises
        body = "<html><body><p>Café résumé</p></body></html>".encode("latin-1")
        h.web.route("example.com", "/a", respond(body=body, content_type="text/html; charset=idna"))
        result = await h.fetch("https://example.com/a")
        assert result.status == STATUS_FETCHED
        assert result.charset_source != "header"
        assert "Caf" in (result.text() or "")

    async def test_s3b_a_malformed_url_is_coded_not_raised(self, h: Harness) -> None:
        result = await h.fetch("https://[zz/p")
        assert result.status in (STATUS_REFUSED, STATUS_FAILED)
        assert (await h.rows())[0].failure_code == result.failure_code

    async def test_s3b_any_internal_error_is_coded_and_written(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.web_research import fetch as fetch_module

        async def boom(self: Any, result: Any, target: str) -> Any:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(fetch_module._OpenWebFetch, "_walk", boom)
        result = await h.fetch("https://example.com/a")
        assert (result.status, result.failure_code) == (STATUS_FAILED, "transport_error")
        assert len(await h.rows()) == 1

    def test_s4_an_unstorable_url_never_falls_back_to_raw_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.web_research import fetch as fetch_module

        def broken(_url: str) -> str:
            raise ValueError("bad")

        monkeypatch.setattr(fetch_module, "stored_url", broken)
        assert fetch_module._stored("https://user:pw@example.com/?token=s") == "<unparseable-url>"


class TestReviewLow:
    @pytest.mark.parametrize(
        ("value", "reserved"),
        [
            ("noai", True),
            ("otherbot: noai, noindex", False),
            ("InvestingBuddy-Research-Bot: noai", True),
            ("otherbot: noindex, *: noai", True),
            ("unavailable_after: 2027-01-01, noai", True),
            ("noindex", False),
        ],
    )
    def test_x_robots_tag_is_scoped_to_our_token(self, value: str, reserved: bool) -> None:
        assert robots.x_robots_tag_noai(value) is reserved

    @pytest.mark.parametrize(
        "host",
        [
            "sci-hub.se",
            "libgen.rs",
            "annas-archive.org",
            "nitter.poast.org",
            "example-com.translate.goog",
            "www-example-com.cdn.ampproject.org",
            "r.jina.ai",
            "youtu.be",
            "hotcopper.com.au",
            "news.ycombinator.com",
            "freedium.cfd",
        ],
    )
    def test_denylist_additions(self, host: str) -> None:
        assert denylisted(host)

    def test_denylist_label_rule_is_not_a_substring_rule(self) -> None:
        assert not denylisted("scihub-news.com") and not denylisted("sci-hub")


# --------------------------------------------------------------------------- #
# Review round 2
# --------------------------------------------------------------------------- #


class TestRound2DbSafeStrings:
    async def test_r2_1_a_nul_in_a_declared_canonical_never_reaches_a_row(self, h: Harness) -> None:
        body = b'<!doctype html><html><head><link rel="canonical" href="/a\x00b"></head>' + (
            HTML_PAGE
        )
        h.web.route("example.com", "/p", respond(body=body))
        result = await h.fetch("https://example.com/p")
        assert result.status == STATUS_FETCHED
        row = (await h.rows())[0]
        assert row.redirect_chain_json[-1]["meta"]["rel_canonical"] == "<unparseable-url>"

    @pytest.mark.parametrize("url", ["https://example.com/a\x00b", "https://example.com/\udcff"])
    async def test_r2_1_nul_and_surrogate_urls_are_refused_and_stored_safely(
        self, h: Harness, url: str
    ) -> None:
        result = await h.fetch(url)
        assert result.status == STATUS_REFUSED
        rows = await h.rows()  # the flush succeeded and the session still works
        assert rows[0].requested_url == "<unparseable-url>"
        assert h.web.requests == []
        h.web.route("example.com", "/ok", respond())
        assert (await h.fetch("https://example.com/ok")).status == STATUS_FETCHED

    def test_r2_1_db_text_and_json_helpers(self) -> None:
        from app.services.web_research import fetch as fetch_module

        assert fetch_module._db_text("e\x00t\udcffag") == "e�t�ag"
        assert fetch_module._db_json({"k\x00": ["v\x00"]}) == {"k�": ["v�"]}
        assert fetch_module._db_url("https://x/\x00") == "<unparseable-url>"


class TestRound2RobotsCapsFailSafe:
    def test_r2_2_allows_past_the_cap_cannot_hide_a_disallow(self) -> None:
        text = "User-agent: *\n" + "Allow: /pub\n" * 2500 + "Disallow: /\n"
        policy = robots.parse_robots(text)
        assert policy.decide("/secret") == robots.ROBOTS_DISALLOWED
        assert policy.truncated

    def test_r2_2_an_over_long_disallow_is_truncated_not_ignored(self) -> None:
        policy = robots.parse_robots("User-agent: *\nDisallow: /" + "a" * 3000 + "\n")
        assert policy.decide("/" + "a" * 2000) == robots.ROBOTS_DISALLOWED

    def test_r2_2_an_over_long_allow_is_dropped(self) -> None:
        policy = robots.parse_robots("User-agent: *\nDisallow: /a\nAllow: /a" + "b" * 3000 + "\n")
        assert policy.decide("/a" + "b" * 3000) == robots.ROBOTS_DISALLOWED

    def test_r2_2_too_many_disallows_fails_closed(self) -> None:
        text = "User-agent: *\n" + "".join(f"Disallow: /p{i}\n" for i in range(2500))
        policy = robots.parse_robots(text)
        assert policy.decide("/anything") == robots.ROBOTS_DISALLOWED
        assert policy.decide("/robots.txt") == robots.ROBOTS_ALLOWED

    def test_r2_5_unmatched_groups_are_not_normalised_and_emoji_is_cheap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        emoji = "\U0001f600" * 100
        other = "User-agent: OtherBot\n" + f"Disallow: /{emoji}\n" * 1200
        ours = "User-agent: *\n" + f"Disallow: /{emoji}\n" * 1000
        calls = 0
        real = robots.normalise_path

        def counting(path: str) -> str:
            nonlocal calls
            calls += 1
            return real(path)

        monkeypatch.setattr(robots, "normalise_path", counting)
        started = time.perf_counter()
        policy = robots.parse_robots(other + ours)
        elapsed = time.perf_counter() - started
        assert calls == 1000  # only the group that applies to us
        assert len(policy.rules) == 1000
        assert elapsed < 15.0  # backstop only; the count above is the real check
        assert real("/plain/path?q=1") == "/plain/path?q=1"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/caf\u00e9", "/caf%C3%A9"),
            ("/a b", "/a%20b"),
            ("/%7euser/%2f", "/~user/%2F"),
            ("/%e2%82%ac\u20ac", "/%E2%82%AC%E2%82%AC"),
        ],
    )
    def test_r2_5_normalisation_is_unchanged(self, raw: str, expected: str) -> None:
        assert robots.normalise_path(raw) == expected


class TestRound2XRobotsTag:
    def test_r2_3_scope_does_not_carry_across_headers(self) -> None:
        assert robots.x_robots_tag_noai("otherbot: noindex\nnoai")
        assert not robots.x_robots_tag_noai("otherbot: noindex, noai")

    async def test_r2_3_two_headers_through_the_fetch(self, h: Harness) -> None:
        def two_headers(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers=[
                    ("content-type", "text/html"),
                    ("x-robots-tag", "otherbot: noindex"),
                    ("x-robots-tag", "noai"),
                ],
                content=HTML_PAGE,
            )

        h.web.route("example.com", "/a", two_headers)
        result = await h.fetch("https://example.com/a")
        assert result.failure_code == "tdm_reserved"
        assert "noai" in result.tdm_signals


class TestRound2Imperva:
    async def test_r2_4_the_imperva_resource_script_is_not_a_captcha(self, h: Harness) -> None:
        body = (
            "<html><head><script src='/_Incapsula_Resource?SWJIYLWA=719d34d31c8e'></script>"
            f"</head><body>{ARTICLE_900}</body></html>"
        ).encode()
        h.web.route("example.com", "/rns", respond(body=body))
        assert (await h.fetch("https://example.com/rns")).status == STATUS_FETCHED

    async def test_r2_4_the_imperva_incident_page_is_a_captcha(self, h: Harness) -> None:
        body = (
            b"<html><body><iframe src='/_Incapsula_Resource?CWUDNSAI=24&xinfo=1'></iframe>"
            b"Request unsuccessful. Incapsula incident ID: 123-456</body></html>"
        )
        h.web.route("example.com", "/x", respond(body=body))
        assert (await h.fetch("https://example.com/x")).failure_code == "captcha"


class TestRound2PolicyFileAudit:
    async def test_r2_6_a_robots_request_cut_by_the_run_deadline_writes_a_row(
        self, h: Harness
    ) -> None:
        async def slow_robots(_r: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1.5)
            return httpx.Response(404)

        h.web.route("example.com", "/robots.txt", slow_robots)
        result = await h.fetch(
            "https://example.com/a", cfg=_cfg(source_document_total_deadline_seconds=0.3)
        )
        assert result.failure_code == "fetch_timeout"
        robots_rows = [r for r in await h.all_rows() if r.origin == "robots"]
        assert [(r.status, r.failure_code) for r in robots_rows] == [("failed", "run_deadline")]
