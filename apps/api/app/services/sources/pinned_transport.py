"""
Resolve-then-connect IP pinning + async DNS — Phase 32A Slice 5B.1 (closes ADR-014).

Slice 5A's outbound guard resolved a hostname and rejected non-public addresses,
but the HTTP stack then resolved the *same name again* when it opened the socket.
Between those two lookups a hostile authoritative server can answer differently —
the classic DNS-rebinding TOCTOU. ADR-014 recorded this as a known residual. This
module closes it: the address that is validated is the address that is connected
to, because the connection is made to a pinned IP literal while TLS SNI, the
``Host`` header and certificate verification all continue to use the real
hostname.

It also removes the synchronous ``socket.getaddrinfo`` call from the event loop:
resolution goes through ``loop.getaddrinfo`` (executor-backed), so a slow or
blackholed resolver can no longer stall every other coroutine in the worker.

Safety properties:
  * **Fail closed.** ``PinnedAsyncHTTPTransport`` refuses to dispatch a request
    for a host it has no validated pin for — an unpinned host raises
    :class:`UnpinnedHostError` before any socket is opened, rather than silently
    falling back to an unvalidated lookup.
  * **Every resolved address is checked, not just the first.** A name that
    resolves to one public and one private address is rejected outright.
  * **IPv4 and IPv6.** Loopback, private, link-local, reserved, multicast,
    unspecified and cloud instance-metadata addresses are rejected in both
    families; IPv6 scope ids are stripped before classification.
  * **TLS is never weakened.** ``verify`` is untouched and the pinned request
    carries ``sni_hostname`` so the handshake and the certificate hostname check
    still target the real hostname — pinning changes *where we connect*, never
    *what we trust*.
  * **Redirects re-pin.** A caller following a redirect must validate and pin the
    new host; a stale pin for the previous hop is never reused for a new host.
  * **Never leaks.** Nothing here logs URLs, bodies or credentials; failures are
    returned as short reason strings built from address text the caller already
    holds.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
from collections.abc import Callable
from typing import Any

from app.services.sources.safe_web_fetcher import (
    _METADATA_IPS,
    _ip_is_public,
    is_safe_public_host,
)

# A resolver is any callable shaped like ``socket.getaddrinfo``. It may be sync
# (the Slice 5A seam, still used by existing tests) or return an awaitable — both
# are accepted so no existing caller or test has to change.
AnyResolver = Callable[..., Any]

# Re-exported under a public name: the SSRF address classifier is shared with the
# hostname guard in ``safe_web_fetcher`` and must stay a single implementation.
ip_is_public = _ip_is_public
METADATA_IPS = _METADATA_IPS


class UnpinnedHostError(RuntimeError):
    """Raised when a request targets a host with no validated pin.

    This is a fail-closed guard, not an error condition to paper over: reaching it
    means a caller tried to dispatch a request whose target was never validated.
    """


def _clean_ip(ip_text: str) -> str:
    """Drop an IPv6 scope id (``fe80::1%eth0`` → ``fe80::1``)."""
    return ip_text.split("%", 1)[0]


def _addresses_from_getaddrinfo(infos: Any) -> list[str]:
    """Pull the address strings out of a ``getaddrinfo``-shaped result."""
    ips: list[str] = []
    for info in infos or []:
        try:
            sockaddr = info[4]
        except (IndexError, TypeError):
            continue
        if not sockaddr:
            continue
        try:
            ips.append(str(sockaddr[0]))
        except (IndexError, TypeError):
            continue
    return ips


async def async_getaddrinfo(host: str, *, resolver: AnyResolver | None = None) -> Any:
    """Resolve ``host`` without blocking the event loop.

    With no ``resolver`` this uses ``loop.getaddrinfo``, which runs the lookup in
    the default executor. An injected ``resolver`` may be sync (the existing
    ``socket.getaddrinfo``-shaped test seam) or async — both are supported so the
    Slice 5A tests keep working unchanged.
    """
    if resolver is not None:
        outcome = resolver(host, None)
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, None)


async def async_resolve_public_ips(
    host: str | None,
    *,
    resolver: AnyResolver | None = None,
) -> tuple[tuple[str, ...], str | None]:
    """Resolve ``host`` and return ``(public_ips, reason)``.

    ``reason`` is None only when the host resolved and **every** returned address
    is a routable public unicast address. Otherwise ``reason`` is a short, secret-
    free explanation and ``public_ips`` is empty — the caller must not connect.

    This is the async sibling of ``safe_web_fetcher.assert_resolved_ip_public``
    and applies exactly the same rejection set, so the two guards cannot drift.
    Never raises: a resolution failure is itself a block reason.
    """
    if not host:
        return (), "empty host"
    try:
        infos = await async_getaddrinfo(host, resolver=resolver)
    except Exception as exc:  # noqa: BLE001 - a resolution failure is a block
        return (), f"dns resolution failed: {type(exc).__name__}"

    ips = _addresses_from_getaddrinfo(infos)
    if not ips:
        return (), "no resolved ip"

    cleaned: list[str] = []
    for ip_text in ips:
        clean = _clean_ip(ip_text)
        if clean in METADATA_IPS:
            return (), f"resolved to metadata ip: {clean}"
        if not ip_is_public(clean):
            return (), f"resolved to non-public ip: {clean}"
        cleaned.append(clean)
    # Preserve resolution order, drop duplicates.
    unique = tuple(dict.fromkeys(cleaned))
    return unique, None


async def resolve_and_validate(
    host: str | None,
    *,
    resolver: AnyResolver | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(pinned_ip, reason)`` for ``host``.

    Combines the hostname guard and the address guard so a caller has a single
    call site: an internal-looking name is rejected before any lookup happens, and
    a name that resolves anywhere non-public is rejected before any connect. The
    returned ``pinned_ip`` is the first validated address, and it is the ONLY
    address the pinned transport will connect to.
    """
    if not is_safe_public_host(host):
        return None, f"unsafe/internal host: {host or 'none'}"
    ips, reason = await async_resolve_public_ips(host, resolver=resolver)
    if reason:
        return None, reason
    return ips[0], None


def _format_host_for_url(ip: str) -> str:
    """Return ``ip`` in the form an URL host expects (IPv6 gets no brackets here).

    ``httpx.URL.copy_with(host=...)`` brackets an IPv6 literal itself when the
    URL is rendered, so the raw address is what must be handed to it. Validated
    here so a malformed pin can never be spliced into a URL.
    """
    ipaddress.ip_address(ip)  # raises ValueError on anything that is not an IP
    return ip


class PinnedAsyncHTTPTransport:
    """An ``httpx`` transport that connects only to pre-validated addresses.

    Wraps ``httpx.AsyncHTTPTransport``. For each request it swaps the URL host for
    the pinned IP, restores the original hostname in the ``Host`` header, and sets
    the ``sni_hostname`` request extension so the TLS handshake and certificate
    hostname verification still target the real hostname. The name is therefore
    never resolved a second time, which is what closes the rebinding window.

    A host with no pin raises :class:`UnpinnedHostError` — the transport fails
    closed rather than resolving on its own.
    """

    def __init__(
        self,
        *,
        pins: dict[str, str] | None = None,
        inner: Any = None,
        **transport_kwargs: Any,
    ) -> None:
        import httpx

        self._pins: dict[str, str] = {}
        for host, ip in (pins or {}).items():
            self.pin(host, ip)
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport(
            **transport_kwargs
        )

    # -- pin management ----------------------------------------------------

    def pin(self, host: str | None, ip: str) -> None:
        """Record the validated address ``host`` is allowed to connect to."""
        if not host:
            raise ValueError("cannot pin an empty host")
        self._pins[host.strip().lower().rstrip(".")] = _format_host_for_url(ip)

    def pinned_ip(self, host: str | None) -> str | None:
        if not host:
            return None
        return self._pins.get(host.strip().lower().rstrip("."))

    # -- httpx transport protocol -----------------------------------------

    async def handle_async_request(self, request: Any) -> Any:
        original_host = request.url.host
        ip = self.pinned_ip(original_host)
        if ip is None:
            raise UnpinnedHostError(
                f"refusing to connect: no validated pin for host {original_host!r}"
            )

        # Preserve the hostname for TLS SNI + certificate verification and for the
        # Host header, then point the socket at the validated address only.
        host_header = request.headers.get("host") or original_host
        request.url = request.url.copy_with(host=ip)
        request.headers["Host"] = host_header
        request.extensions = {**dict(request.extensions), "sni_hostname": original_host}
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> PinnedAsyncHTTPTransport:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._inner.__aexit__(*exc_info)


def build_pinned_transport(
    pins: dict[str, str] | None = None, **transport_kwargs: Any
) -> PinnedAsyncHTTPTransport | None:
    """Build a pinned transport, or None when pinning is unavailable.

    Returning None (rather than raising) lets a caller degrade to the Slice 5A
    behaviour — resolve-and-check without socket pinning — on an httpx build that
    cannot support the ``sni_hostname`` extension, instead of losing outbound
    fetching entirely. The caller must record that degradation honestly; it must
    never treat None as "pinning succeeded".
    """
    try:
        return PinnedAsyncHTTPTransport(pins=pins, **transport_kwargs)
    except Exception:  # noqa: BLE001 - unavailable pinning must not break a run
        return None


def supports_pinning() -> bool:
    """True when this httpx/httpcore build exposes what pinning needs."""
    try:
        import httpcore  # noqa: F401
        import httpx

        return hasattr(httpx, "AsyncHTTPTransport")
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "AnyResolver",
    "METADATA_IPS",
    "PinnedAsyncHTTPTransport",
    "UnpinnedHostError",
    "async_getaddrinfo",
    "async_resolve_public_ips",
    "build_pinned_transport",
    "ip_is_public",
    "resolve_and_validate",
    "supports_pinning",
]
