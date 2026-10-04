"""W0 — fetch hardening + truthful search labels (open-web threat model §2.3, D1-D14).

Implements acceptance SSRF-01…SSRF-25 (docs/open-web-research-acceptance-plan.md §2.1)
plus the D7/D8/D12/D14 and spec §22.3 cases.

FULLY OFFLINE. There is no real DNS and no real socket: names resolve through a fake
``getaddrinfo``-shaped resolver, and the pinned transport's inner transport is an
``httpx.MockTransport`` that records every request that REACHED a target. The pass
condition for every refusal is the one the acceptance plan states: the URL is refused
**before** any request reaches the target — asserted as "the mock network recorded no
request to that host".

Most refusals run with ``source_connector_allowlist_only=False``: that is the open-web
mode W2 will enable, and it proves the GUARD refused — not the allowlist.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.services.sources import pinned_transport as pinned_module
from app.services.sources.document_fetcher import safe_fetch_document
from app.services.sources.ingestion_status import (
    FAILURE_FETCH_TIMEOUT,
    FAILURE_RESPONSE_TOO_LARGE,
)
from app.services.sources.redaction import strip_url_secrets, url_has_secret
from app.services.sources.safe_web_fetcher import (
    MAX_URL_LENGTH,
    _ip_is_public,
    check_url_shape,
    extract_links,
    is_safe_public_host,
    mixed_script_host,
    normalize_host,
    open_web_fetch_refusal,
    python_runtime_is_safe,
    safe_fetch_page,
)

PUBLIC_A = "93.184.216.34"
PUBLIC_B = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


# --------------------------------------------------------------------------- #
# Offline network: fake DNS + a recording mock transport behind the real pin
# --------------------------------------------------------------------------- #


def _info(ip: str) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr: tuple[Any, ...] = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


class FakeDNS:
    """A ``getaddrinfo``-shaped resolver. A list value is returned in order per call."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.calls: list[str] = []
        self._served: dict[str, int] = {}

    def __call__(self, host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
        self.calls.append(host)
        entry = self.table.get(host)
        if entry is None:
            raise socket.gaierror(f"no such host: {host}")
        if isinstance(entry, tuple):  # a sequence of answers: rebinding
            n = self._served.get(host, 0)
            self._served[host] = n + 1
            answer = entry[min(n, len(entry) - 1)]
        else:
            answer = entry
        return [_info(ip) for ip in answer]


class MockNet:
    """Routes by the ``Host`` header (the pinned transport keeps it) and records."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def hosts(self) -> list[str]:
        return [r.headers.get("host", "") for r in self.requests]

    def connected_ips(self) -> list[str]:
        return [r.url.host for r in self.requests]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", "")
        route = self.routes.get(host)
        if route is None:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")
        return route(request) if callable(route) else route


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> MockNet:
    """Every guarded fetch in this file connects through the REAL pinned transport,
    whose inner transport is the recording mock. Nothing can reach a socket."""
    network = MockNet()

    def _build(pins: dict[str, str] | None = None, **_kw: Any) -> Any:
        return pinned_module.PinnedAsyncHTTPTransport(
            pins=pins, transport_factory=lambda: httpx.MockTransport(network.handler)
        )

    monkeypatch.setattr(pinned_module, "build_pinned_transport", _build)
    return network


def _cfg(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "source_connector_allowlist_only": False,  # open-web mode: the GUARD decides
        "primary_document_pin_dns_enabled": True,
        "source_connector_max_bytes": 1_000_000,
        "source_document_extraction_max_bytes": 35_000_000,
        "source_fetch_total_deadline_seconds": 30.0,
        "source_document_total_deadline_seconds": 30.0,
    }
    values.update(overrides)
    return Settings(**values)


def _redirect(location: str, **headers: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": location, **headers})


def _streamed(headers: dict[str, str], body: bytes, chunk: int = 16_384) -> Any:
    """A route serving ``body`` as a network would: streamed, not pre-read, so the raw
    (still-encoded) bytes are what the fetcher receives."""

    def route(_request: httpx.Request) -> httpx.Response:
        async def gen():
            for i in range(0, len(body), chunk):
                yield body[i : i + chunk]

        return httpx.Response(200, headers=headers, content=gen())

    return route


async def _page(url: str, dns: FakeDNS, cfg: Settings | None = None, allowed=()) -> Any:
    return await safe_fetch_page(
        url, allowed_domains=allowed, cfg=cfg or _cfg(), resolve_ip=True, resolver=dns
    )


async def _doc(url: str, dns: FakeDNS, cfg: Settings | None = None, allowed=()) -> Any:
    return await safe_fetch_document(
        url, allowed_domains=allowed, cfg=cfg or _cfg(), resolve_ip=True, resolver=dns
    )


def _refused_before_network(result: Any, net: MockNet, *, host: str | None = None) -> None:
    assert result.blocked is True, result.error
    if host is None:
        assert net.requests == [], "a request reached the network before refusal"
    else:
        assert host not in net.hosts(), f"a request reached {host} before refusal"


# --------------------------------------------------------------------------- #
# SSRF-01 … SSRF-05  literal / metadata / WireServer targets
# --------------------------------------------------------------------------- #


class TestLiteralTargets:
    async def test_ssrf_01_ipv4_loopback_literal(self, net: MockNet) -> None:
        dns = FakeDNS({})
        result = await _page("https://127.0.0.1/", dns)
        _refused_before_network(result, net)
        assert dns.calls == [], "an IP literal must be refused before any lookup"

    @pytest.mark.parametrize("url", ["https://localhost/", "https://LOCALHOST./"])
    async def test_ssrf_02_localhost(self, net: MockNet, url: str) -> None:
        dns = FakeDNS({"localhost": [PUBLIC_A]})  # even if DNS lied, the name is refused
        result = await _page(url, dns)
        _refused_before_network(result, net)
        assert dns.calls == []

    async def test_ssrf_03_instance_metadata(self, net: MockNet) -> None:
        result = await _doc("https://169.254.169.254/latest/meta-data/", FakeDNS({}))
        _refused_before_network(result, net)

    async def test_ssrf_04_app_service_identity_endpoint(self, net: MockNet) -> None:
        result = await _doc("https://169.254.130.1:8081/msi/token", FakeDNS({}))
        _refused_before_network(result, net)
        # And a NAME resolving into 169.254/16 is refused too (all of it, D2).
        dns = FakeDNS({"identity.example.com": ["169.254.130.1"]})
        result = await _doc("https://identity.example.com/msi/token", dns)
        _refused_before_network(result, net)

    async def test_ssrf_05_wireserver_is_refused_as_literal_and_as_resolution(
        self, net: MockNet
    ) -> None:
        result = await _page("https://168.63.129.16/", FakeDNS({}))
        _refused_before_network(result, net)
        # D2: 168.63.129.16 is a PUBLIC address; `is_private` never caught it.
        dns = FakeDNS({"wire.example.com": ["168.63.129.16"]})
        result = await _page("https://wire.example.com/machine?comp=goalstate", dns)
        _refused_before_network(result, net)
        assert "non-public ip" in (result.error or "")


# --------------------------------------------------------------------------- #
# SSRF-06 … SSRF-09  names that resolve internally; encoded hosts; IPv6
# --------------------------------------------------------------------------- #


class TestResolution:
    @pytest.mark.parametrize("ip", ["10.0.0.5", "172.16.0.1", "192.168.1.1"])
    async def test_ssrf_06_rfc1918(self, net: MockNet, ip: str) -> None:
        dns = FakeDNS({"intranet.example.com": [ip]})
        result = await _page("https://intranet.example.com/", dns)
        _refused_before_network(result, net)

    async def test_ssrf_07_cgnat(self, net: MockNet) -> None:
        # D1: `_ip_is_public('100.64.0.1')` was True before W0.
        assert _ip_is_public("100.64.0.1") is False
        dns = FakeDNS({"cgnat.example.com": ["100.64.0.1"]})
        result = await _doc("https://cgnat.example.com/ar.pdf", dns)
        _refused_before_network(result, net)

    @pytest.mark.parametrize(
        "host", ["2130706433", "0x7f000001", "0177.0.0.1", "127.1", "0x7f.1", "1.0x2"]
    )
    async def test_ssrf_08_encoded_ipv4_hosts(self, net: MockNet, host: str) -> None:
        # D3: `is_safe_public_host('2130706433')` was True before W0.
        assert is_safe_public_host(host) is False
        dns = FakeDNS({host: ["127.0.0.1"]})
        result = await _page(f"https://{host}/", dns)
        _refused_before_network(result, net)
        assert dns.calls == [], "an encoded IP must be refused before DNS"

    @pytest.mark.parametrize(
        "literal",
        [
            "::1",
            "fe80::1",
            "fd00::1",
            "::ffff:127.0.0.1",
            "64:ff9b::a00:1",
            "2002:7f00:1::",
        ],
    )
    async def test_ssrf_09_ipv6_literals_and_resolutions(
        self, net: MockNet, literal: str
    ) -> None:
        result = await _page(f"https://[{literal}]/", FakeDNS({}))
        _refused_before_network(result, net)
        # The same addresses arriving through DNS: unwrapped and refused.
        assert _ip_is_public(literal) is False
        dns = FakeDNS({"v6.example.com": [literal]})
        result = await _page("https://v6.example.com/", dns)
        _refused_before_network(result, net)

    @pytest.mark.parametrize(
        "ip",
        [
            "0.1.2.3",
            "192.0.0.8",
            "192.0.2.1",
            "192.88.99.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "::",
            "fec0::1",
            "ff02::1",
            "100::1",
            "2001:db8::1",
            "3fff::1",
            "64:ff9b:1::a00:1",
            "2001:0:4136:e378:8000:63bf:80ff:fffe",  # Teredo carrying 127.0.0.1
            "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        ],
    )
    def test_the_explicit_denylist(self, ip: str) -> None:
        assert _ip_is_public(ip) is False, ip

    @pytest.mark.parametrize("ip", [PUBLIC_A, PUBLIC_V6, "8.8.8.8", "64:ff9b::808:808"])
    def test_real_public_addresses_still_pass(self, ip: str) -> None:
        assert _ip_is_public(ip) is True, ip


# --------------------------------------------------------------------------- #
# SSRF-10 … SSRF-15  redirects, rebinding, multi-record, hop limit, denied suffixes
# --------------------------------------------------------------------------- #


class TestRedirects:
    async def test_ssrf_10_redirect_to_loopback(self, net: MockNet) -> None:
        net.routes["www.example.com"] = _redirect("https://127.0.0.1/")
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _page("https://www.example.com/", dns)
        assert result.blocked and "redirect blocked" in (result.error or "")
        assert net.hosts() == ["www.example.com"], "only the public hop was requested"

    async def test_ssrf_11_https_to_http_downgrade(self, net: MockNet) -> None:
        net.routes["www.example.com"] = _redirect("http://example.com/")
        dns = FakeDNS({"www.example.com": [PUBLIC_A], "example.com": [PUBLIC_B]})
        result = await _doc("https://www.example.com/ar.pdf", dns)
        assert result.blocked and "non-https" in (result.error or "")
        assert net.hosts() == ["www.example.com"]

    async def test_ssrf_12_rebinding_uses_the_validated_ip_and_rechecks_each_hop(
        self, net: MockNet
    ) -> None:
        # First answer public, every later answer private.
        dns = FakeDNS({"rebind.example.com": ([PUBLIC_A], ["127.0.0.1"])})
        net.routes["rebind.example.com"] = _redirect("https://rebind.example.com/next")
        result = await _page("https://rebind.example.com/start", dns)
        # The connection went to the FIRST validated address, never re-resolved…
        assert net.connected_ips() == [PUBLIC_A]
        # …and the redirect hop was re-resolved, re-validated and refused.
        assert result.blocked and "redirect blocked" in (result.error or "")
        assert dns.calls == ["rebind.example.com", "rebind.example.com"]
        assert len(net.requests) == 1

    async def test_ssrf_13_one_public_one_private_record(self, net: MockNet) -> None:
        dns = FakeDNS({"mixed.example.com": [PUBLIC_A, "10.0.0.1"]})
        result = await _page("https://mixed.example.com/", dns)
        _refused_before_network(result, net)

    async def test_ssrf_14_four_hop_chain_is_refused_at_hop_four(
        self, net: MockNet
    ) -> None:
        hosts = [f"h{i}.example.com" for i in range(5)]
        for i in range(4):
            net.routes[hosts[i]] = _redirect(f"https://{hosts[i + 1]}/")
        dns = FakeDNS({h: [PUBLIC_A] for h in hosts})
        result = await _page(f"https://{hosts[0]}/", dns)
        assert result.blocked and result.error == "too many redirects"
        assert net.hosts() == hosts[:4], "the 4th redirect target was never requested"

    async def test_ssrf_14_redirect_loop(self, net: MockNet) -> None:
        net.routes["loop.example.com"] = _redirect("https://loop.example.com/")
        dns = FakeDNS({"loop.example.com": [PUBLIC_A]})
        result = await _doc("https://loop.example.com/", dns)
        assert result.blocked and result.error == "too many redirects"
        assert len(net.requests) == 4

    @pytest.mark.parametrize(
        "target",
        [
            "kv-prod.vault.azure.net",
            "ib-stg-psql.postgres.database.azure.com",
            "ib-stg-api.scm.azurewebsites.net",
            "ibstgdocs.blob.core.windows.net",
            "ib-stg-api.azurewebsites.net",
            "ib-stg-web.azurewebsites.net",
            "own-app.example.com",  # WEBSITE_HOSTNAME, set by App Service
        ],
    )
    async def test_ssrf_15_redirect_to_a_denied_platform_host(
        self, net: MockNet, monkeypatch: pytest.MonkeyPatch, target: str
    ) -> None:
        from app.core.config import settings as live_settings

        monkeypatch.setenv("WEBSITE_HOSTNAME", "own-app.example.com")
        # Only THIS platform's artifact-store account is refused, not every blob host.
        monkeypatch.setattr(
            live_settings,
            "v3_artifact_store_account_url",
            "https://ibstgdocs.blob.core.windows.net",
        )
        net.routes["www.example.com"] = _redirect(f"https://{target}/secrets")
        # Even resolving to a PUBLIC address, the host itself is refused.
        dns = FakeDNS({"www.example.com": [PUBLIC_A], target: [PUBLIC_B]})
        result = await _page("https://www.example.com/", dns)
        assert result.blocked and "redirect blocked" in (result.error or "")
        assert target not in net.hosts()
        assert target not in dns.calls, "refused on the name, before any lookup"


# --------------------------------------------------------------------------- #
# SSRF-16 … SSRF-22  URL shape, parser agreement, IDN, proxies, single-label
# --------------------------------------------------------------------------- #


class TestUrlShape:
    async def test_ssrf_16_userinfo(self, net: MockNet) -> None:
        dns = FakeDNS({"example.com": [PUBLIC_A]})
        result = await _page("https://user:pass@example.com/", dns)
        _refused_before_network(result, net)
        assert "userinfo" in (result.error or "")
        assert dns.calls == []

    async def test_ssrf_17_non_443_port(self, net: MockNet) -> None:
        dns = FakeDNS({"example.com": [PUBLIC_A]})
        result = await _doc("https://example.com:8443/", dns)
        _refused_before_network(result, net)
        assert "port 8443" in (result.error or "")
        # The explicit default port is the same URL and is allowed.
        assert check_url_shape("https://example.com:443/x") == (None, "example.com")

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com/_x",
            "data:text/plain,hello",
            "javascript:alert(1)",
            "http://example.com/",
        ],
    )
    async def test_ssrf_18_other_schemes(self, net: MockNet, url: str) -> None:
        dns = FakeDNS({"example.com": [PUBLIC_A]})
        result = await _page(url, dns)
        _refused_before_network(result, net)
        assert dns.calls == []

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com\\@127.0.0.1/",
            "https://127.0.0.1#@example.com/",
            "https://exa mple.com/",
            "https://example.com/a b",
            "https://example.com\t/",
            "https://example.com/\x00",
            "https://example.com/\r\nX-Injected: 1",
            "https://example.com/\x7f",
            "https://example.com/" + "a" * MAX_URL_LENGTH,
        ],
    )
    async def test_ssrf_19_parser_differential_and_forbidden_characters(
        self, net: MockNet, url: str
    ) -> None:
        dns = FakeDNS({"example.com": [PUBLIC_A]})
        result = await _page(url, dns)
        _refused_before_network(result, net)

    def test_ssrf_20_idn_homograph_is_normalised_and_flagged(self) -> None:
        homograph = "аpple.com"  # Cyrillic "а" + Latin "pple"
        assert normalize_host(homograph) == "xn--pple-43d.com"
        assert check_url_shape(f"https://{homograph}/")[1] == "xn--pple-43d.com"
        assert mixed_script_host(homograph) is True
        assert mixed_script_host("xn--pple-43d.com") is True, "punycode is decoded first"
        assert mixed_script_host("apple.com") is False
        assert mixed_script_host("münchen.de") is False  # one script, not a homograph

    async def test_ssrf_20_the_normalised_host_is_what_gets_resolved(
        self, net: MockNet
    ) -> None:
        dns = FakeDNS({"xn--pple-43d.com": ["10.0.0.9"]})
        result = await _page("https://аpple.com/", dns)
        assert dns.calls == ["xn--pple-43d.com"]
        _refused_before_network(result, net)

    async def test_ssrf_21_environment_proxy_is_not_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """D6. With pinning OFF there is no custom transport, so an httpx client with
        ``trust_env=True`` WOULD mount the environment proxy. The guarded client must
        not."""
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
        real_client = httpx.AsyncClient
        # The probe itself: under this environment trust_env=True mounts a proxy.
        assert real_client(trust_env=True)._mounts, "the test would not detect a proxy"

        seen: list[dict[str, Any]] = []
        mock = MockNet()

        class RecordingClient(real_client):  # type: ignore[misc,valid-type]
            def __init__(self, **kw: Any) -> None:
                probe = real_client(**{k: v for k, v in kw.items() if k != "transport"})
                seen.append({"trust_env": kw.get("trust_env"), "mounts": dict(probe._mounts)})
                kw.setdefault("transport", httpx.MockTransport(mock.handler))
                super().__init__(**kw)

        monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        cfg = _cfg(primary_document_pin_dns_enabled=False)
        page = await _page("https://www.example.com/", dns, cfg)
        doc = await _doc("https://www.example.com/ar.html", dns, cfg)
        assert page.error is None and doc.ok
        assert seen and all(s["trust_env"] is False for s in seen)
        assert all(s["mounts"] == {} for s in seen), "an environment proxy was mounted"

    @pytest.mark.parametrize(
        "host",
        ["intranet", "db.internal", "printer.local", "router.home.arpa", "box.lan"],
    )
    async def test_ssrf_22_single_label_and_internal_suffixes(
        self, net: MockNet, host: str
    ) -> None:
        dns = FakeDNS({host: [PUBLIC_A]})
        result = await _page(f"https://{host}/", dns)
        _refused_before_network(result, net)
        assert dns.calls == []


# --------------------------------------------------------------------------- #
# SSRF-23  the press-release path (D9)
# --------------------------------------------------------------------------- #

RSS = (
    b'<?xml version="1.0"?><rss><channel><item><title>Q2 results</title>'
    b"<link>https://news.issuer-example.com/q2</link></item></channel></rss>"
)


class TestPressReleaseProvider:
    async def test_ssrf_23_redirect_to_a_private_address_is_refused(
        self, net: MockNet
    ) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        net.routes["news.issuer-example.com"] = _redirect(
            "https://feeds.news.issuer-example.com/rss"
        )
        dns = FakeDNS(
            {
                "news.issuer-example.com": [PUBLIC_A],
                "feeds.news.issuer-example.com": ["10.0.0.7"],
            }
        )
        provider = CompanyPressReleaseProvider(resolver=dns, cfg=_cfg())
        assert await provider._fetch("https://news.issuer-example.com/rss") is None
        assert net.hosts() == ["news.issuer-example.com"]

    async def test_redirect_to_a_loopback_literal_or_another_site_is_refused(
        self, net: MockNet
    ) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        dns = FakeDNS({"news.issuer-example.com": [PUBLIC_A], "evil.example": [PUBLIC_B]})
        # Production setting: the candidate's own host is the allowlist, so a redirect
        # to another site is refused even though that site resolves publicly.
        provider = CompanyPressReleaseProvider(
            resolver=dns, cfg=_cfg(source_connector_allowlist_only=True)
        )
        for location in ("https://127.0.0.1/rss", "https://evil.example/rss"):
            net.requests.clear()
            net.routes["news.issuer-example.com"] = _redirect(location)
            assert await provider._fetch("https://news.issuer-example.com/rss") is None
            assert net.hosts() == ["news.issuer-example.com"]

    async def test_a_feed_is_read_over_https_even_from_an_http_candidate(
        self, net: MockNet
    ) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        net.routes["news.issuer-example.com"] = httpx.Response(
            200, headers={"content-type": "application/rss+xml"}, content=RSS
        )
        dns = FakeDNS({"news.issuer-example.com": [PUBLIC_A]})
        provider = CompanyPressReleaseProvider(resolver=dns, cfg=_cfg())
        text = await provider._fetch("http://news.issuer-example.com/rss")
        assert text is not None and "<rss>" in text
        assert [r.url.scheme for r in net.requests] == ["https"], "never in the clear"

    async def test_a_resolved_private_feed_host_is_never_contacted(
        self, net: MockNet
    ) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        dns = FakeDNS({"news.issuer-example.com": ["192.168.0.10"]})
        provider = CompanyPressReleaseProvider(resolver=dns, cfg=_cfg())
        assert await provider._fetch("https://news.issuer-example.com/rss") is None
        assert net.requests == []


# --------------------------------------------------------------------------- #
# SSRF-24  runtime Python version (D13)
# --------------------------------------------------------------------------- #


class TestRuntimeVersion:
    def test_ssrf_24_the_assertion_helper(self) -> None:
        assert python_runtime_is_safe((3, 12, 3)) is False
        assert python_runtime_is_safe((3, 11, 9)) is False
        assert python_runtime_is_safe((3, 12, 4)) is True
        assert python_runtime_is_safe((3, 13, 0)) is True
        assert open_web_fetch_refusal((3, 12, 3)) is not None
        assert open_web_fetch_refusal((3, 12, 4)) is None

    def test_ssrf_24_this_runtime_is_safe(self) -> None:
        # CI runs 3.12.x; a runtime below 3.12.4 must fail here, loudly.
        assert tuple(sys.version_info)[:3] >= (3, 12, 4)
        assert python_runtime_is_safe() is True

    async def test_an_unsafe_runtime_refuses_the_open_web_fetch_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.agent_tools.external import FETCH_PUBLIC_SOURCE_SPEC
        from app.services.providers import leads as leads_module
        from app.services.sources import safe_web_fetcher

        monkeypatch.setattr(
            safe_web_fetcher, "python_runtime_is_safe", lambda version_info=None: False
        )
        called: list[Any] = []

        async def _never(*a: Any, **kw: Any) -> Any:
            called.append(a)
            raise AssertionError("verify_lead must not run on an unsafe runtime")

        monkeypatch.setattr(leads_module, "verify_lead", _never)
        ctx = _ToolCtx(_cfg())
        payload = await FETCH_PUBLIC_SOURCE_SPEC.handler(
            ctx,
            FETCH_PUBLIC_SOURCE_SPEC.validate_arguments(
                {"url": "https://www.example.com/x", "claim": "Revenue was 5"}
            ),
        )
        assert payload["refused"] is True and payload["items"] == []
        assert "3.12.4" in payload["summary"]
        assert called == []

    def test_startup_check_logs_and_never_raises(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.services.sources import safe_web_fetcher

        monkeypatch.setattr(safe_web_fetcher.sys, "version_info", (3, 12, 3, "final", 0))
        with caplog.at_level("WARNING"):
            assert safe_web_fetcher.log_python_runtime_check() is False
        assert "python_runtime_below_safe_minimum" in caplog.text


# --------------------------------------------------------------------------- #
# SSRF-25 / D14  no cookie carried across hops
# --------------------------------------------------------------------------- #


class TestCookies:
    @pytest.mark.parametrize("fetch", [_page, _doc])
    async def test_ssrf_25_hop_two_receives_no_cookie(self, net: MockNet, fetch: Any) -> None:
        net.routes["a.example.com"] = _redirect(
            "https://b.example.org/next",
            **{"set-cookie": "session=hop1secret; Domain=example.org; Path=/"},
        )
        def _b(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/next":
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://b.example.org/final",
                        "set-cookie": "again=hop2secret; Path=/",
                    },
                )
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

        net.routes["b.example.org"] = _b
        dns = FakeDNS({"a.example.com": [PUBLIC_A], "b.example.org": [PUBLIC_B]})
        await fetch("https://a.example.com/start", dns)
        assert net.hosts() == ["a.example.com", "b.example.org", "b.example.org"]
        for request in net.requests:
            assert "cookie" not in request.headers, "a cookie was replayed to a hop"
            assert "authorization" not in request.headers


# --------------------------------------------------------------------------- #
# D7 decompression bound · D8 total deadline
# --------------------------------------------------------------------------- #


class TestBudgets:
    async def test_identity_is_requested(self, net: MockNet) -> None:
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        await _page("https://www.example.com/", dns)
        assert net.requests[0].headers["accept-encoding"] == "identity"

    async def test_a_gzip_bomb_never_inflates_past_the_page_cap(self, net: MockNet) -> None:
        bomb = gzip.compress(b"<html>" + b"A" * 40_000_000)
        net.routes["www.example.com"] = _streamed(
            {"content-type": "text/html", "content-encoding": "gzip"}, bomb
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        cfg = _cfg(source_connector_max_bytes=200_000)
        result = await _page("https://www.example.com/", dns, cfg)
        assert result.body_html is not None
        assert len(result.body_html.encode()) == 200_000, "the cap is exact, no overshoot"

    async def test_a_gzip_bomb_document_is_refused_on_ratio(self, net: MockNet) -> None:
        bomb = gzip.compress(b"%PDF-1.4 " + b"\0" * 40_000_000)
        assert len(bomb) < 100_000
        net.routes["www.example.com"] = _streamed(
            {"content-type": "application/pdf", "content-encoding": "gzip"}, bomb
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/ar.pdf", dns)
        assert result.blocked is True and result.content is None
        assert result.failure_code == FAILURE_RESPONSE_TOO_LARGE
        assert "decompression ratio" in (result.error or "")

    async def test_an_honest_gzip_body_still_decodes(self, net: MockNet) -> None:
        body = b"<html><title>Annual report</title>" + b"<p>text</p>" * 500 + b"</html>"
        net.routes["www.example.com"] = _streamed(
            {"content-type": "text/html", "content-encoding": "gzip"},
            gzip.compress(body),
            chunk=64,
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/ar.html", dns)
        assert result.ok and result.content == body

    async def test_an_unboundable_encoding_is_refused(self, net: MockNet) -> None:
        net.routes["www.example.com"] = _streamed(
            {"content-type": "text/html", "content-encoding": "br"}, b"\x0b\x02\x80hi\x03"
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/ar.html", dns)
        assert result.blocked and result.content is None

    @pytest.mark.parametrize("fetch", [_page, _doc])
    async def test_a_slow_drip_is_stopped_by_the_total_deadline(
        self, net: MockNet, fetch: Any
    ) -> None:
        async def drip():
            yield b"<html>"
            while True:  # one byte, well inside any per-read timeout, forever
                await asyncio.sleep(0.05)
                yield b"x"

        net.routes["slow.example.com"] = lambda _r: httpx.Response(
            200, headers={"content-type": "text/html"}, content=drip()
        )
        dns = FakeDNS({"slow.example.com": [PUBLIC_A]})
        cfg = _cfg(
            source_fetch_total_deadline_seconds=0.4,
            source_document_total_deadline_seconds=0.4,
        )
        started = time.monotonic()
        result = await fetch("https://slow.example.com/", dns, cfg)
        assert time.monotonic() - started < 5
        assert "deadline" in (result.error or "")
        if hasattr(result, "failure_code"):
            assert result.failure_code == FAILURE_FETCH_TIMEOUT

    def test_the_deadline_setting_exists_and_has_a_reader(self) -> None:
        from app.services.sources.safe_web_fetcher import fetch_total_deadline_seconds

        cfg = Settings(source_fetch_total_deadline_seconds=12.5)
        assert fetch_total_deadline_seconds(cfg) == 12.5


# --------------------------------------------------------------------------- #
# D12  secrets are stripped from what is STORED, never from what is FETCHED
# --------------------------------------------------------------------------- #


class TestSecretStripping:
    @pytest.mark.parametrize("name", ["countrycode", "sortkey", "design", "author", "page"])
    def test_whole_names_survive(self, name: str) -> None:
        url = f"https://www.example.com/r?{name}=1"
        assert strip_url_secrets(url) == url
        assert url_has_secret(url) is False

    @pytest.mark.parametrize(
        "name",
        [
            "token",
            "api_key",
            "apiKey",
            "access_token",
            "X-Amz-Signature",
            "sig",
            "code",
            "client_secret",
            "apitoken",
            "password",
        ],
    )
    def test_credential_names_are_stripped(self, name: str) -> None:
        url = f"https://www.example.com/r?keep=1&{name}=S"
        assert strip_url_secrets(url) == "https://www.example.com/r?keep=1"

    def test_a_link_keeps_its_signature_for_the_fetch_only(self) -> None:
        html = '<a href="/ar-2025.pdf?countrycode=GB&sig=abc">Annual report</a>'
        [link] = extract_links(
            html,
            base_url="https://www.example.com/ir",
            allowed_domains=("example.com",),
            keywords=("annual report",),
            max_links=5,
        )
        assert link.url == "https://www.example.com/ar-2025.pdf?countrycode=GB"
        assert link.fetch_target == "https://www.example.com/ar-2025.pdf?countrycode=GB&sig=abc"
        assert "sig=abc" not in repr(link), "the raw URL never reaches a repr"

    async def test_the_fetched_url_is_unmodified_and_the_stored_one_is_stripped(
        self, net: MockNet
    ) -> None:
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc(
            "https://www.example.com/ar.html?countrycode=GB&token=T0K", dns
        )
        assert net.requests[0].url.query == b"countrycode=GB&token=T0K"
        assert result.requested_url == "https://www.example.com/ar.html?countrycode=GB"
        assert result.final_url == "https://www.example.com/ar.html?countrycode=GB"


# --------------------------------------------------------------------------- #
# D3 — the one live document fetch that ran without resolving
# --------------------------------------------------------------------------- #


async def test_live_document_extractor_now_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.sources import live_fetchers

    seen: dict[str, Any] = {}

    async def _fake(url: str, **kw: Any) -> Any:
        seen.update(kw)
        from app.services.sources.document_fetcher import DocumentFetchResult

        return DocumentFetchResult(requested_url=url, blocked=True, error="x")

    monkeypatch.setattr(live_fetchers, "safe_fetch_document", _fake)
    await live_fetchers.live_document_extractor(
        "https://www.example.com/ar.pdf", allowed_domains=("example.com",), cfg=_cfg()
    )
    assert seen.get("resolve_ip") is True


async def test_a_fetch_outside_the_allowlist_resolves_even_if_not_asked(
    net: MockNet,
) -> None:
    """W0 / D3: with the allowlist off, ``resolve_ip=False`` cannot skip the address
    check — the resolver is consulted and a private answer refuses the fetch."""
    dns = FakeDNS({"www.example.com": ["10.1.2.3"]})
    result = await safe_fetch_document(
        "https://www.example.com/ar.pdf",
        allowed_domains=(),
        cfg=_cfg(),
        resolve_ip=False,
        resolver=dns,
    )
    _refused_before_network(result, net)
    assert dns.calls == ["www.example.com"]


# --------------------------------------------------------------------------- #
# D11 + robots token
# --------------------------------------------------------------------------- #


class TestTraversalDomain:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://www.pensana.co.uk/investors", "pensana.co.uk"),
            ("https://ir.issuer.com.au/reports", "issuer.com.au"),
            ("https://investors.example.com/", "example.com"),
            ("https://tenant.azurewebsites.net/", "tenant.azurewebsites.net"),
            ("https://co.uk/", None),
        ],
    )
    def test_registrable_domain_uses_the_public_suffix_list(
        self, url: str, expected: str | None
    ) -> None:
        from app.services.traversal.issuer_site import registrable_domain_of

        assert registrable_domain_of(url) == expected

    def test_robots_agent_is_the_token_actually_sent(self) -> None:
        from app.services.sources.safe_web_fetcher import _USER_AGENT
        from app.services.traversal.issuer_site import ROBOTS_AGENT

        assert ROBOTS_AGENT == "InvestingBuddy-Research-Bot"
        assert _USER_AGENT.startswith(ROBOTS_AGENT + "/")

    def test_a_robots_rule_naming_the_real_token_is_obeyed(self) -> None:
        from urllib.robotparser import RobotFileParser

        from app.services.traversal.issuer_site import ROBOTS_AGENT

        parser = RobotFileParser()
        parser.parse(["User-agent: InvestingBuddy-Research-Bot", "Disallow: /private"])
        assert parser.can_fetch(ROBOTS_AGENT, "https://x.example.com/private/a") is False
        assert parser.can_fetch(ROBOTS_AGENT, "https://x.example.com/public") is True


# --------------------------------------------------------------------------- #
# Spec §22.3 — recall is never labelled search
# --------------------------------------------------------------------------- #

FINDINGS = (
    '{"findings": [{"claim": "Total revenue was $145 million in Q2 2026", '
    '"source_url": "https://investors.modernatx.com/quarterly-results", "value": "145", '
    '"unit": "USD million", "period": "2026-Q2", "scope": "group", '
    '"publisher": "Moderna"}]}'
)


def _fixture_response(*, keep_search_calls: bool) -> Any:
    from app.integrations.deepseek.transport import HttpDeepSeekTransport

    path = Path(__file__).parent / "fixtures" / "deepseek_responses_web_search.json"
    body = json.loads(path.read_text())
    if not keep_search_calls:
        body["output"] = [o for o in body["output"] if o.get("type") != "web_search_call"]
    for item in body["output"]:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if "text" in part:
                    part["text"] = FINDINGS
    return HttpDeepSeekTransport._reduce_responses(body)


class _FixtureTransport:
    model = "deepseek-v4-flash"
    search_tool_name = "web_search"

    def __init__(self, response: Any) -> None:
        self._response = response

    async def investigate_with_search(self, **_kw: Any) -> Any:
        return self._response


class _ToolCtx:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.session = None
        self.company_id = None
        self.legal_entity_id = None
        self.research_job_id = None
        self.role = "external_research_analyst"
        self.task_ref = None
        self.search_backend = None


class TestTruthfulSearchLabels:
    async def test_zero_search_calls_is_model_recall(self) -> None:
        from app.integrations.deepseek.providers import DeepSeekResearchProvider

        response = _fixture_response(keep_search_calls=False)
        assert response.tool_payloads == []
        result = await DeepSeekResearchProvider(
            transport=_FixtureTransport(response), search_enabled=True
        ).investigate(question="What was Moderna's Q2 2026 revenue?")
        assert result.research_leads, "the synthesised answer still yields a lead"
        meta = result.raw_provider_metadata
        assert meta["discovery_mode"] == "model_recall"
        assert meta["executed_search_queries"] == 0
        assert meta["retrieval_backed"] is False
        assert any(w.startswith("web_search_unavailable") for w in result.warnings)

    async def test_the_recorded_search_is_labelled_search(self) -> None:
        from app.integrations.deepseek.providers import DeepSeekResearchProvider

        result = await DeepSeekResearchProvider(
            transport=_FixtureTransport(_fixture_response(keep_search_calls=True)),
            search_enabled=True,
        ).investigate(question="q")
        assert result.raw_provider_metadata["discovery_mode"] == "search"
        assert result.raw_provider_metadata["executed_search_queries"] == 2
        assert not any("web_search_unavailable" in w for w in result.warnings)

    async def test_search_web_is_no_longer_backed_by_a_model_that_searches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W5 (decision U12): ``search_web`` queries the configured SearchProvider. The
        DeepSeek research provider is never asked, so recall can no longer be mistaken for
        a search there; the recall labelling above still guards the Discovery paths."""
        import app.services.agents.routing as routing
        from app.services.agent_tools.external import SEARCH_WEB_SPEC

        class _Exploding:
            async def investigate(self, **kw):  # noqa: ANN003, ANN201
                raise AssertionError("search_web must not ask a DeepSeek model to search")

        monkeypatch.setattr(routing, "research_provider_for", lambda _cfg: _Exploding())
        payload = await SEARCH_WEB_SPEC.handler(
            _ToolCtx(_cfg()), {"query": "Moderna Q2 2026 revenue", "domains": []}
        )
        assert payload["available"] is False
        assert payload["web_search"] == "web_search_unavailable"
        assert payload["leads"] == [] and payload["candidates"] == []

    async def test_recall_path_without_search_enabled_is_recall(self) -> None:
        from app.integrations.deepseek.providers import DeepSeekResearchProvider
        from app.integrations.deepseek.transport import FakeDeepSeekTransport

        result = await DeepSeekResearchProvider(
            transport=FakeDeepSeekTransport(completion_text=FINDINGS),
            search_enabled=False,
        ).investigate(question="q")
        assert result.raw_provider_metadata["discovery_mode"] == "model_recall"

    def test_the_search_timeout_setting_is_defined(self) -> None:
        assert Settings(v3_external_search_timeout_seconds=42).v3_external_search_timeout_seconds == 42
        assert Settings.model_fields["v3_external_search_timeout_seconds"].default == 180


# =========================================================================== #
# W0 review round 1
# =========================================================================== #


class TestReviewRound1:
    # -- B1: whitespace in links and Location headers is encoded, not refused --

    def test_b1_a_spaced_href_becomes_an_encoded_link(self) -> None:
        html = '<a href="/investors/Annual Report 2024.pdf">Annual report 2024</a>'
        [link] = extract_links(
            html,
            base_url="https://www.example.com/ir",
            allowed_domains=("example.com",),
            keywords=("annual report",),
            max_links=5,
        )
        assert link.url == "https://www.example.com/investors/Annual%20Report%202024.pdf"
        assert check_url_shape(link.fetch_target)[0] is None

    async def test_b1_a_spaced_href_is_fetched(self, net: MockNet) -> None:
        net.routes["www.example.com"] = lambda r: (
            httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b'<a href="/docs/Annual Report 2024.pdf">Annual report</a>',
            )
            if r.url.path == "/ir"
            else httpx.Response(200, headers={"content-type": "application/pdf"},
                                content=b"%PDF-1.4 x")
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        page = await _page("https://www.example.com/ir", dns, allowed=("example.com",))
        [link] = page.links
        doc = await _doc(link.fetch_target, dns)
        assert doc.ok
        assert net.requests[-1].url.raw_path == b"/docs/Annual%20Report%202024.pdf"

    @pytest.mark.parametrize("fetch", [_page, _doc])
    async def test_b1_a_spaced_location_is_followed(self, net: MockNet, fetch: Any) -> None:
        net.routes["www.example.com"] = lambda r: (
            # As a real server sends it: UTF-8 bytes on the wire (space + NBSP).
            httpx.Response(
                302, headers=[(b"location", "/files/Half Year\u00a0Report.pdf".encode())]
            )
            if r.url.path == "/start"
            else httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await fetch("https://www.example.com/start", dns)
        assert result.error is None and not result.blocked
        assert net.requests[-1].url.raw_path == b"/files/Half%20Year%C2%A0Report.pdf"

    @pytest.mark.parametrize(
        "location", ["https://exa mple.com/x", "https://example.com\\@127.0.0.1/", "https://a\x00b.example.com/"]
    )
    async def test_b1_whitespace_or_controls_in_the_authority_stay_refused(
        self, net: MockNet, location: str
    ) -> None:
        net.routes["www.example.com"] = _redirect(location)
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _page("https://www.example.com/", dns)
        # Refused by our guard, or (a NUL in a header) by httpx itself — either way
        # nothing past the first hop is ever requested.
        assert result.error and not result.body_html
        assert len(net.requests) <= 1

    # -- M1: Content-Encoding is a list --

    @pytest.mark.parametrize(
        "header, expected",
        [
            ("", []),
            ("identity", []),
            ("none", []),
            ("identity, gzip", ["gzip"]),
            ("GZIP , foo", ["gzip"]),
            ("aws-chunked", []),
            ("gzip, gzip", ["gzip", "gzip"]),
            ("br", ["br"]),
        ],
    )
    def test_m1_content_encoding_tokens(self, header: str, expected: list[str]) -> None:
        from app.services.sources.safe_web_fetcher import content_encodings

        assert content_encodings(header) == expected

    @pytest.mark.parametrize(
        "header, ok",
        [("identity", True), ("aws-chunked", True), ("identity, gzip", True),
         ("gzip, gzip", False), ("zstd", False)],
    )
    async def test_m1_bodies_by_encoding(self, net: MockNet, header: str, ok: bool) -> None:
        body = b"<html>" + b"x" * 1000 + b"</html>"
        wire = gzip.compress(body) if "gzip" in header and ok else body
        net.routes["www.example.com"] = _streamed(
            {"content-type": "text/html", "content-encoding": header}, wire
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/a.html", dns)
        assert result.ok is ok
        if ok:
            assert result.content == body

    async def test_multi_member_gzip_is_decoded_completely(self, net: MockNet) -> None:
        wire = gzip.compress(b"<html>first ") + gzip.compress(b"second</html>")
        net.routes["www.example.com"] = _streamed(
            {"content-type": "text/html", "content-encoding": "gzip"}, wire, chunk=7
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/a.html", dns)
        assert result.ok and result.content == b"<html>first second</html>"

    # -- M2: separate budgets; the first DNS lookup is inside the budget --

    def test_m2_documents_have_their_own_longer_deadline(self) -> None:
        from app.services.sources.safe_web_fetcher import fetch_total_deadline_seconds

        cfg = Settings()
        assert fetch_total_deadline_seconds(cfg) == 90.0
        assert fetch_total_deadline_seconds(cfg, kind="document") == 180.0

    @pytest.mark.parametrize("fetch", [_page, _doc])
    async def test_m2_a_hanging_first_lookup_is_bounded(
        self, net: MockNet, fetch: Any
    ) -> None:
        async def _hang(host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
            await asyncio.sleep(30)
            return [_info(PUBLIC_A)]

        cfg = _cfg(
            source_fetch_total_deadline_seconds=0.3,
            source_document_total_deadline_seconds=0.3,
        )
        started = time.monotonic()
        result = await fetch("https://www.example.com/", _hang, cfg)
        assert time.monotonic() - started < 5
        assert "deadline" in (result.error or "") and net.requests == []
        if hasattr(result, "failure_code"):
            assert result.failure_code == FAILURE_FETCH_TIMEOUT

    # -- M4: feeds on a sibling sub-domain; declared charset --

    async def test_m4_feed_may_redirect_within_its_registrable_domain(
        self, net: MockNet
    ) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        net.routes["www.issuer.co.uk"] = _redirect("https://news.issuer.co.uk/rss")
        latin1 = (
            '<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel><item>'
            "<title>Résultats</title></item></channel></rss>"
        ).encode("latin-1")
        net.routes["news.issuer.co.uk"] = httpx.Response(
            200, headers={"content-type": "application/rss+xml"}, content=latin1
        )
        dns = FakeDNS({"www.issuer.co.uk": [PUBLIC_A], "news.issuer.co.uk": [PUBLIC_B]})
        provider = CompanyPressReleaseProvider(
            resolver=dns, cfg=_cfg(source_connector_allowlist_only=True)
        )
        text = await provider._fetch("https://www.issuer.co.uk/rss")
        assert text is not None and "Résultats" in text
        assert net.hosts() == ["www.issuer.co.uk", "news.issuer.co.uk"]

    async def test_m4_feed_may_not_leave_its_registrable_domain(self, net: MockNet) -> None:
        from app.integrations.providers.company_press_release_provider import (
            CompanyPressReleaseProvider,
        )

        net.routes["www.issuer.co.uk"] = _redirect("https://other.co.uk/rss")
        dns = FakeDNS({"www.issuer.co.uk": [PUBLIC_A], "other.co.uk": [PUBLIC_B]})
        provider = CompanyPressReleaseProvider(
            resolver=dns, cfg=_cfg(source_connector_allowlist_only=True)
        )
        assert await provider._fetch("https://www.issuer.co.uk/rss") is None
        assert net.hosts() == ["www.issuer.co.uk"]

    # -- S-M1: IPv4-compatible and SIIT forms --

    @pytest.mark.parametrize("ip", ["::a9fe:a9fe", "::127.0.0.1", "::ffff:0:7f00:1", "::8.8.8.8"])
    def test_s_m1_ipv4_compatible_and_siit_are_not_public(self, ip: str) -> None:
        assert _ip_is_public(ip) is False

    # -- S-M2: discovery candidates keep their raw URL for the fetch --

    def test_s_m2_discovered_documents_fetch_the_url_as_published(self) -> None:
        import dataclasses

        from app.services.sources.document_discovery import discover_documents

        html = (
            '<script type="application/ld+json">{"url": '
            '"https://www.example.com/docs/Annual Report 2025.pdf?sig=abc&countrycode=GB",'
            ' "name": "Annual report 2025"}</script>'
        )
        [doc] = discover_documents(
            html, base_url="https://www.example.com/ir", allowed_domains=("example.com",)
        )
        assert doc.url == "https://www.example.com/docs/Annual%20Report%202025.pdf?countrycode=GB"
        assert doc.fetch_target == (
            "https://www.example.com/docs/Annual%20Report%202025.pdf?sig=abc&countrycode=GB"
        )
        assert "sig=abc" not in repr(doc)
        assert "sig=abc" not in json.dumps(dataclasses.asdict(doc))

    # -- S-M3: the stored form strips aggressively; the fetched form never --

    @pytest.mark.parametrize(
        "name",
        [
            "authkey", "accesskey", "secretkey", "privatekey", "subscriptionkey",
            "sessionkey", "authorization", "authcode", "accesscode", "apisig",
            "awsaccesskeyid", "AWSAccessKeyId", "X-Goog-Credential",
        ],
    )
    def test_s_m3_compound_credential_names_are_stripped(self, name: str) -> None:
        url = f"https://www.example.com/r?keep=1&{name}=S"
        assert strip_url_secrets(url) == "https://www.example.com/r?keep=1"

    # -- LOW: stored link form is canonical; the raw URL never serialises --

    def test_stored_link_drops_userinfo_and_fragment(self) -> None:
        html = '<a href="https://www.example.com/ar.pdf#page=3">Annual report</a>'
        [link] = extract_links(
            html,
            base_url="https://www.example.com/ir",
            allowed_domains=("example.com",),
            keywords=("annual report",),
            max_links=5,
        )
        assert link.url == "https://www.example.com/ar.pdf"

    def test_safelink_raw_url_is_not_a_field(self) -> None:
        import dataclasses
        import pickle

        from app.services.sources.safe_web_fetcher import SafeLink

        link = SafeLink(url="https://a.example.com/x", text="t",
                        fetch_url="https://a.example.com/x?sig=S")
        assert "sig=S" not in json.dumps(dataclasses.asdict(link))
        assert "sig=S" not in repr(link) and "sig=S" not in str(dataclasses.astuple(link))
        assert link == SafeLink(url="https://a.example.com/x", text="t")
        # In-process copies keep the fetch target (pickle is never persisted).
        assert pickle.loads(pickle.dumps(link)).fetch_target.endswith("sig=S")

    async def test_bare_web_search_calls_are_not_evidence_of_search(self) -> None:
        from types import SimpleNamespace

        from app.services.agent_tools.external import _discovery_mode_of

        result = SimpleNamespace(
            raw_provider_metadata={}, consumption=SimpleNamespace(web_search_calls=3)
        )
        assert _discovery_mode_of(result) == ("model_recall", 0)
        failed = SimpleNamespace(
            raw_provider_metadata={"trace": {"query_call_count": 2,
                                             "failed_query_call_count": 2}},
            consumption=None,
        )
        assert _discovery_mode_of(failed) == ("model_recall", 0)

    # -- LOW: D13 applies to every open-web entry point --

    async def test_open_web_mode_refuses_on_an_unsafe_runtime(
        self, net: MockNet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.sources import safe_web_fetcher

        monkeypatch.setattr(
            safe_web_fetcher, "python_runtime_is_safe", lambda version_info=None: False
        )
        dns = FakeDNS({"www.example.com": [PUBLIC_A]})
        result = await _doc("https://www.example.com/a.pdf", dns)  # allowlist off
        _refused_before_network(result, net)
        assert "3.12.4" in (result.error or "")
        # An allowlisted fetch still runs on the explicit denylist.
        ok = await _doc(
            "https://www.example.com/a.html", dns,
            _cfg(source_connector_allowlist_only=True), allowed=("example.com",),
        )
        assert ok.ok

    async def test_verify_lead_public_web_refuses_on_an_unsafe_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.providers.contracts import LEAD_REJECTED, ResearchLead
        from app.services.providers.leads import verify_lead
        from app.services.sources import safe_web_fetcher

        monkeypatch.setattr(
            safe_web_fetcher, "python_runtime_is_safe", lambda version_info=None: False
        )

        async def _never(*a: Any, **kw: Any) -> Any:
            raise AssertionError("no fetch on an unsafe runtime")

        lead = ResearchLead(
            claim_text="Revenue was 5", provider="x",
            claimed_source_url="https://www.example.com/x",
        )
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_never, allow_public_web=True
        )
        assert outcome.status == LEAD_REJECTED
        assert "3.12.4" in (outcome.detail or "")

    async def test_the_psl_lookup_runs_off_the_event_loop(self) -> None:
        import threading

        from app.services.sources import public_suffix

        seen: list[str] = []
        original = public_suffix.registrable_domain

        def _spy(host: str | None) -> str | None:
            seen.append(threading.current_thread().name)
            return original(host)

        public_suffix.registrable_domain = _spy  # type: ignore[assignment]
        try:
            assert await public_suffix.aregistrable_domain("www.issuer.co.uk") == "issuer.co.uk"
        finally:
            public_suffix.registrable_domain = original  # type: ignore[assignment]
        assert seen and seen[0] != threading.main_thread().name
