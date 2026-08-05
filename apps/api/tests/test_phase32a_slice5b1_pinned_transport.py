"""
Phase 32A Slice 5B.1 — resolve-then-connect IP pinning + async DNS (ADR-014).

Fully offline: no real DNS, no sockets, no network. The resolver seam is a fake
shaped like ``socket.getaddrinfo`` (and, separately, an async variant), and the
pinned transport is exercised against a fake inner transport that records the
request it was handed.

What these tests protect:
  * every resolved address is classified, not just the first;
  * IPv4 AND IPv6 internal ranges are rejected;
  * an internal-looking hostname is rejected BEFORE any lookup happens;
  * the connection target is the validated IP while TLS SNI + Host stay the real
    hostname (this is what closes the DNS-rebinding TOCTOU);
  * an unpinned host fails closed instead of resolving on its own.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from app.services.sources.pinned_transport import (
    PinnedAsyncHTTPTransport,
    UnpinnedHostError,
    async_resolve_public_ips,
    build_pinned_transport,
    ip_is_public,
    resolve_and_validate,
    supports_pinning,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


# --------------------------------------------------------------------------- #
# Fake resolvers (shaped like socket.getaddrinfo)
# --------------------------------------------------------------------------- #


def _info(ip: str) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr: tuple[Any, ...] = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def _sync_resolver(*ips: str):
    def resolver(host: str, port: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
        return [_info(ip) for ip in ips]

    return resolver


def _recording_sync_resolver(*ips: str):
    seen: list[str] = []

    def resolver(host: str, port: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
        seen.append(host)
        return [_info(ip) for ip in ips]

    return resolver, seen


def _async_resolver(*ips: str):
    async def resolver(host: str, port: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
        return [_info(ip) for ip in ips]

    return resolver


def _raising_resolver(exc: Exception):
    def resolver(host: str, port: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
        raise exc

    return resolver


# --------------------------------------------------------------------------- #
# A. Address classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",
        "::",
        "ff02::1",
    ],
)
def test_ip_is_public_rejects_internal_ranges(ip: str) -> None:
    assert ip_is_public(ip) is False


@pytest.mark.parametrize("ip", [PUBLIC_V4, PUBLIC_V6, "8.8.8.8", "2001:4860:4860::8888"])
def test_ip_is_public_accepts_routable_addresses(ip: str) -> None:
    assert ip_is_public(ip) is True


def test_ip_is_public_rejects_garbage() -> None:
    assert ip_is_public("not-an-ip") is False


# --------------------------------------------------------------------------- #
# B. async_resolve_public_ips
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_accepts_public_ipv4() -> None:
    ips, reason = await async_resolve_public_ips(
        "example.com", resolver=_sync_resolver(PUBLIC_V4)
    )
    assert reason is None
    assert ips == (PUBLIC_V4,)


@pytest.mark.asyncio
async def test_resolve_accepts_public_ipv6() -> None:
    ips, reason = await async_resolve_public_ips(
        "example.com", resolver=_sync_resolver(PUBLIC_V6)
    )
    assert reason is None
    assert ips == (PUBLIC_V6,)


@pytest.mark.asyncio
async def test_resolve_supports_async_resolver_seam() -> None:
    ips, reason = await async_resolve_public_ips(
        "example.com", resolver=_async_resolver(PUBLIC_V4)
    )
    assert reason is None
    assert ips == (PUBLIC_V4,)


@pytest.mark.asyncio
async def test_resolve_rejects_private_ipv4() -> None:
    ips, reason = await async_resolve_public_ips(
        "evil.example.com", resolver=_sync_resolver("10.0.0.5")
    )
    assert ips == ()
    assert reason is not None and "non-public ip" in reason


@pytest.mark.asyncio
async def test_resolve_rejects_private_ipv6() -> None:
    ips, reason = await async_resolve_public_ips(
        "evil.example.com", resolver=_sync_resolver("fd00::1")
    )
    assert ips == ()
    assert reason is not None and "non-public ip" in reason


@pytest.mark.asyncio
async def test_resolve_rejects_metadata_ipv4() -> None:
    _ips, reason = await async_resolve_public_ips(
        "metadata.example.com", resolver=_sync_resolver("169.254.169.254")
    )
    # link-local classification fires first; either way it must be blocked.
    assert reason is not None


@pytest.mark.asyncio
async def test_resolve_rejects_metadata_ipv6() -> None:
    _ips, reason = await async_resolve_public_ips(
        "metadata.example.com", resolver=_sync_resolver("fd00:ec2::254")
    )
    assert reason is not None


@pytest.mark.asyncio
async def test_resolve_rejects_when_ANY_address_is_internal() -> None:
    """A split-horizon answer with one public and one private address is a block."""
    ips, reason = await async_resolve_public_ips(
        "mixed.example.com", resolver=_sync_resolver(PUBLIC_V4, "10.0.0.5")
    )
    assert ips == ()
    assert reason is not None and "non-public ip" in reason


@pytest.mark.asyncio
async def test_resolve_strips_ipv6_scope_id_before_classifying() -> None:
    _ips, reason = await async_resolve_public_ips(
        "scoped.example.com", resolver=_sync_resolver("fe80::1%eth0")
    )
    assert reason is not None and "non-public ip" in reason


@pytest.mark.asyncio
async def test_resolve_handles_resolution_failure() -> None:
    ips, reason = await async_resolve_public_ips(
        "example.com", resolver=_raising_resolver(OSError("boom"))
    )
    assert ips == ()
    assert reason is not None and "dns resolution failed" in reason


@pytest.mark.asyncio
async def test_resolve_handles_empty_answer() -> None:
    ips, reason = await async_resolve_public_ips("example.com", resolver=_sync_resolver())
    assert ips == ()
    assert reason == "no resolved ip"


@pytest.mark.asyncio
async def test_resolve_rejects_empty_host() -> None:
    ips, reason = await async_resolve_public_ips("", resolver=_sync_resolver(PUBLIC_V4))
    assert ips == ()
    assert reason == "empty host"


@pytest.mark.asyncio
async def test_resolve_dedups_repeated_addresses() -> None:
    ips, reason = await async_resolve_public_ips(
        "example.com", resolver=_sync_resolver(PUBLIC_V4, PUBLIC_V4)
    )
    assert reason is None
    assert ips == (PUBLIC_V4,)


# --------------------------------------------------------------------------- #
# C. resolve_and_validate — hostname guard runs BEFORE any lookup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_and_validate_returns_first_public_ip() -> None:
    ip, reason = await resolve_and_validate(
        "example.com", resolver=_sync_resolver(PUBLIC_V4, "8.8.8.8")
    )
    assert reason is None
    assert ip == PUBLIC_V4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["localhost", "metadata.google.internal", "foo.internal", "bar.local"]
)
async def test_resolve_and_validate_rejects_internal_names_without_resolving(
    host: str,
) -> None:
    resolver, seen = _recording_sync_resolver(PUBLIC_V4)
    ip, reason = await resolve_and_validate(host, resolver=resolver)
    assert ip is None
    assert reason is not None and "unsafe/internal host" in reason
    assert seen == []  # the lookup must never have happened


@pytest.mark.asyncio
async def test_resolve_and_validate_rejects_ip_literal_host() -> None:
    resolver, seen = _recording_sync_resolver(PUBLIC_V4)
    ip, reason = await resolve_and_validate("10.0.0.5", resolver=resolver)
    assert ip is None
    assert reason is not None and "unsafe/internal host" in reason
    assert seen == []


@pytest.mark.asyncio
async def test_resolve_and_validate_blocks_rebinding_answer() -> None:
    ip, reason = await resolve_and_validate(
        "reports.richemont.com", resolver=_sync_resolver("10.0.0.5")
    )
    assert ip is None
    assert reason is not None and "non-public ip" in reason


# --------------------------------------------------------------------------- #
# D. PinnedAsyncHTTPTransport
# --------------------------------------------------------------------------- #


class _FakeInner:
    """Stands in for ``httpx.AsyncHTTPTransport`` and records what it was given."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.closed = False

    async def handle_async_request(self, request: Any) -> str:
        self.requests.append(request)
        return "sent"

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> _FakeInner:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _request(url: str) -> Any:
    import httpx

    return httpx.Request("GET", url)


@pytest.mark.asyncio
async def test_pinned_transport_connects_to_pinned_ip_and_keeps_hostname() -> None:
    import httpx

    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    transport.pin("www.sec.gov", PUBLIC_V4)

    request = _request("https://www.sec.gov/Archives/edgar/data/320193/x.htm")
    result = await transport.handle_async_request(request)

    assert result == "sent"
    sent = inner.requests[0]
    # The socket target is the validated address...
    assert sent.url.host == PUBLIC_V4
    # ...while the hostname survives for Host, TLS SNI and cert verification.
    assert sent.headers["Host"] == "www.sec.gov"
    assert sent.extensions["sni_hostname"] == "www.sec.gov"
    # Path/scheme/port are untouched.
    assert sent.url.scheme == "https"
    assert sent.url.path == "/Archives/edgar/data/320193/x.htm"
    assert isinstance(sent, httpx.Request)


@pytest.mark.asyncio
async def test_pinned_transport_supports_ipv6_pin() -> None:
    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    transport.pin("example.com", PUBLIC_V6)

    await transport.handle_async_request(_request("https://example.com/a.pdf"))

    sent = inner.requests[0]
    assert sent.url.host == PUBLIC_V6
    assert sent.headers["Host"] == "example.com"
    assert sent.extensions["sni_hostname"] == "example.com"
    # Rendered back into a URL the IPv6 literal must be bracketed.
    assert "[" in str(sent.url)


@pytest.mark.asyncio
async def test_pinned_transport_fails_closed_for_unpinned_host() -> None:
    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    transport.pin("www.sec.gov", PUBLIC_V4)

    with pytest.raises(UnpinnedHostError):
        await transport.handle_async_request(_request("https://evil.example.com/x.pdf"))

    assert inner.requests == []  # nothing was dispatched


@pytest.mark.asyncio
async def test_pinned_transport_does_not_reuse_a_pin_across_hosts() -> None:
    """A redirect to a new host must be re-validated, not covered by the old pin."""
    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    transport.pin("www.richemont.com", PUBLIC_V4)

    with pytest.raises(UnpinnedHostError):
        await transport.handle_async_request(
            _request("https://reports.richemont.com/ar.pdf")
        )
    assert inner.requests == []

    # Once the new hop is validated and pinned it goes through.
    transport.pin("reports.richemont.com", "8.8.8.8")
    await transport.handle_async_request(_request("https://reports.richemont.com/ar.pdf"))
    assert inner.requests[0].url.host == "8.8.8.8"
    assert inner.requests[0].headers["Host"] == "reports.richemont.com"


def test_pin_normalizes_host_case_and_trailing_dot() -> None:
    transport = PinnedAsyncHTTPTransport(inner=_FakeInner())
    transport.pin("WWW.SEC.GOV.", PUBLIC_V4)
    assert transport.pinned_ip("www.sec.gov") == PUBLIC_V4
    assert transport.pinned_ip("WWW.SEC.gov") == PUBLIC_V4


def test_pin_rejects_empty_host() -> None:
    transport = PinnedAsyncHTTPTransport(inner=_FakeInner())
    with pytest.raises(ValueError):
        transport.pin("", PUBLIC_V4)


def test_pin_rejects_non_ip_value() -> None:
    """A hostname can never be spliced in where a validated address belongs."""
    transport = PinnedAsyncHTTPTransport(inner=_FakeInner())
    with pytest.raises(ValueError):
        transport.pin("example.com", "attacker.example.com")


def test_pinned_ip_returns_none_for_unknown_host() -> None:
    transport = PinnedAsyncHTTPTransport(inner=_FakeInner())
    assert transport.pinned_ip("nope.example.com") is None
    assert transport.pinned_ip(None) is None


def test_constructor_accepts_initial_pins() -> None:
    transport = PinnedAsyncHTTPTransport(
        pins={"example.com": PUBLIC_V4}, inner=_FakeInner()
    )
    assert transport.pinned_ip("example.com") == PUBLIC_V4


@pytest.mark.asyncio
async def test_aclose_closes_inner_transport() -> None:
    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    await transport.aclose()
    assert inner.closed is True


# --------------------------------------------------------------------------- #
# E. Availability helpers
# --------------------------------------------------------------------------- #


def test_supports_pinning_on_this_build() -> None:
    assert supports_pinning() is True


def test_build_pinned_transport_returns_a_transport() -> None:
    transport = build_pinned_transport({"example.com": PUBLIC_V4})
    assert transport is not None
    assert transport.pinned_ip("example.com") == PUBLIC_V4


def test_build_pinned_transport_returns_none_on_bad_pin() -> None:
    """An unusable pin degrades to None (caller keeps the unpinned guard) not a crash."""
    assert build_pinned_transport({"example.com": "not-an-ip"}) is None


@pytest.mark.asyncio
async def test_real_httpx_client_accepts_the_pinned_transport() -> None:
    """The transport must satisfy httpx's transport protocol, not just our fake."""
    import httpx

    inner = _FakeInner()
    transport = PinnedAsyncHTTPTransport(inner=inner)
    transport.pin("example.com", PUBLIC_V4)

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    # Swap the inner for a real httpx MockTransport so the whole client stack runs.
    transport._inner = httpx.MockTransport(_handler)  # noqa: SLF001 - direct seam
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://example.com/x")
    assert resp.status_code == 200
    assert resp.text == "ok"


# --------------------------------------------------------------------------- #
# F. The pinning chain must be REACHABLE from the real fetchers.
#
# PR-review blocker 2: ``live_ir_page_fetcher`` called ``safe_fetch_page`` without
# ``resolve_ip=True``, and ``pinned_transport_for`` returns None whenever
# ``resolve_ip`` is off — so every pinning path above was dead in production. The
# IR page is exactly the fetch whose BODY the discovery strategies parse.
# --------------------------------------------------------------------------- #

IR_URL = "https://www.example.com/investors/reports"
IR_ALLOWED: tuple[str, ...] = ("example.com",)


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"<html><body>ok</body></html>",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self._body = body
        self.is_redirect = 300 <= status_code < 400

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def aiter_bytes(self):
        yield self._body


class _RecordingClient:
    def __init__(self, response: _FakeResponse, **kw: Any) -> None:
        self.kw = kw
        self._response = response
        self.requests: list[str] = []

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def stream(self, method: str, url: str) -> _FakeResponse:
        self.requests.append(url)
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> list[_RecordingClient]:
    import httpx

    created: list[_RecordingClient] = []

    def _factory(**kw: Any) -> _RecordingClient:
        client = _RecordingClient(response, **kw)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return created


@pytest.mark.asyncio
async def test_ir_page_fetch_validates_and_pins_the_resolved_address(monkeypatch) -> None:
    from app.services.sources.live_fetchers import live_ir_page_fetcher

    created = _patch_client(monkeypatch, _FakeResponse())
    result = await live_ir_page_fetcher(
        IR_URL, allowed_domains=IR_ALLOWED, resolver=_sync_resolver(PUBLIC_V4)
    )

    assert result.blocked is False and result.error is None
    transport = created[0].kw.get("transport")
    assert transport is not None, "the IR page fetch was not pinned"
    assert transport.pinned_ip("www.example.com") == PUBLIC_V4


@pytest.mark.asyncio
async def test_ir_page_fetch_is_blocked_when_the_host_resolves_internally(
    monkeypatch,
) -> None:
    from app.services.sources.live_fetchers import live_ir_page_fetcher

    created = _patch_client(monkeypatch, _FakeResponse())
    result = await live_ir_page_fetcher(
        IR_URL, allowed_domains=IR_ALLOWED, resolver=_sync_resolver("10.0.0.5")
    )

    assert result.blocked is True
    assert result.body_html is None and result.links == []
    assert created == [], "a rebound host still opened a connection"


@pytest.mark.asyncio
async def test_ir_page_fetch_rejects_a_mixed_public_private_resolution(
    monkeypatch,
) -> None:
    from app.services.sources.live_fetchers import live_ir_page_fetcher

    created = _patch_client(monkeypatch, _FakeResponse())
    result = await live_ir_page_fetcher(
        IR_URL,
        allowed_domains=IR_ALLOWED,
        resolver=_sync_resolver(PUBLIC_V4, "127.0.0.1"),
    )
    assert result.blocked is True
    assert created == []


# --------------------------------------------------------------------------- #
# G. The pinning kill-switch is honest, not silent (ADR-015).
# --------------------------------------------------------------------------- #


def _doc_cfg(*, pin: bool):
    from app.core.config import Settings

    cfg = Settings()
    cfg.primary_document_ingestion_enabled = True
    cfg.primary_document_pin_dns_enabled = pin
    return cfg


@pytest.mark.asyncio
async def test_document_fetch_records_pinning_when_enabled(monkeypatch) -> None:
    from app.services.sources.document_fetcher import safe_fetch_document

    created = _patch_client(
        monkeypatch,
        _FakeResponse(headers={"content-type": "text/html"}, body=b"<html>hi</html>"),
    )
    result = await safe_fetch_document(
        "https://www.example.com/ar.html",
        allowed_domains=IR_ALLOWED,
        cfg=_doc_cfg(pin=True),
        resolve_ip=True,
        resolver=_sync_resolver(PUBLIC_V4),
    )
    assert result.ok is True
    assert result.pinned is True
    assert created[0].kw.get("transport") is not None


@pytest.mark.asyncio
async def test_pin_dns_kill_switch_off_still_fetches_and_reports_unpinned(
    monkeypatch,
) -> None:
    """Flag off ⇒ ``pinned`` False and the fetch still succeeds (degraded, honest)."""
    from app.services.sources.document_fetcher import safe_fetch_document

    created = _patch_client(
        monkeypatch,
        _FakeResponse(headers={"content-type": "text/html"}, body=b"<html>hi</html>"),
    )
    result = await safe_fetch_document(
        "https://www.example.com/ar.html",
        allowed_domains=IR_ALLOWED,
        cfg=_doc_cfg(pin=False),
        resolve_ip=True,
        resolver=_sync_resolver(PUBLIC_V4),
    )
    # Still fetched — the kill-switch degrades to Slice 5A behaviour…
    assert result.ok is True
    assert result.content == b"<html>hi</html>"
    # …and the degradation is RECORDED, never claimed as pinned.
    assert result.pinned is False
    assert created[0].kw.get("transport") is None
    # The address guard itself is unaffected by the kill-switch.
    assert result.blocked is False


@pytest.mark.asyncio
async def test_kill_switch_off_does_not_disable_the_address_guard(monkeypatch) -> None:
    from app.services.sources.document_fetcher import safe_fetch_document

    created = _patch_client(monkeypatch, _FakeResponse())
    result = await safe_fetch_document(
        "https://www.example.com/ar.html",
        allowed_domains=IR_ALLOWED,
        cfg=_doc_cfg(pin=False),
        resolve_ip=True,
        resolver=_sync_resolver("169.254.169.254"),
    )
    assert result.blocked is True
    assert result.pinned is False
    assert created == []


@pytest.mark.asyncio
async def test_artifact_carries_the_pinned_flag_end_to_end(monkeypatch) -> None:
    """Blocker 3: ``pinned`` reaches the artifact and the durable attempt record."""
    from app.services.sources.ingestion_attempts import attempts_for_primary_documents
    from app.services.sources.live_fetchers import live_primary_document_extractor

    _patch_client(
        monkeypatch,
        _FakeResponse(
            headers={"content-type": "text/html"},
            body=b"<html><body><p>" + b"Revenue was reported. " * 40 + b"</p></body></html>",
        ),
    )
    artifact = await live_primary_document_extractor(
        "https://www.example.com/ar.html",
        allowed_domains=IR_ALLOWED,
        cfg=_doc_cfg(pin=True),
        resolver=_sync_resolver(PUBLIC_V4),
    )
    assert artifact.pinned is True
    record = attempts_for_primary_documents([artifact])[0]
    assert record.pinned is True


@pytest.mark.asyncio
async def test_unfetched_artifact_reports_pinned_as_unknown() -> None:
    """No fetch happened ⇒ NULL, not a claim that pinning did or did not happen."""
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
    from app.services.sources.ingestion_attempts import attempts_for_primary_documents

    artifact = PrimaryDocumentArtifact(
        source_url="https://www.example.com/ar.pdf",
        status="extraction_failed",
        failure_code="budget_exhausted",
    )
    assert artifact.pinned is None
    assert attempts_for_primary_documents([artifact])[0].pinned is None
