"""Registrable-domain lookup against the Public Suffix List — W0 (threat model D11).

A host's *registrable domain* is the public suffix plus one label: ``www.pensana.co.uk``
→ ``pensana.co.uk``, ``ir.issuer.com.au`` → ``issuer.com.au``. Taking "the last two
labels" instead turns a ``.co.uk`` start URL into an allowlist for all of ``co.uk``.

WHY ``publicsuffixlist``
========================
It is a small, pure-Python package that ships a snapshot of the PSL **inside the wheel**
and never touches the network at runtime (it only downloads when explicitly asked to,
which nothing here does). ``tldextract`` fetches the list over HTTP by default and
caches it on disk — a runtime network call and a writable cache path this platform
does not want. The snapshot is refreshed by upgrading the pinned package.

The FULL list is used, private section included: ``azurewebsites.net``,
``cloudfront.net`` and ``github.io`` are public suffixes there, so one tenant's site
never allowlists every other tenant's.

Never raises: a host the list cannot place returns None and the caller treats it as
"no registrable domain" (nothing is allowlisted).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _psl() -> Any:
    from publicsuffixlist import PublicSuffixList

    # Default constructor: the bundled snapshot, ICANN + private sections. No I/O
    # beyond reading the packaged data file.
    return PublicSuffixList()


def _clean(host: str | None) -> str | None:
    if not host:
        return None
    h = host.strip().lower().rstrip(".")
    return h or None


def public_suffix(host: str | None) -> str | None:
    """The public suffix of ``host`` (``co.uk`` for ``www.issuer.co.uk``), or None."""
    h = _clean(host)
    if h is None:
        return None
    try:
        suffix = _psl().publicsuffix(h)
    except Exception:  # noqa: BLE001 - a malformed host has no suffix
        return None
    return str(suffix) if suffix else None


def registrable_domain(host: str | None) -> str | None:
    """The registrable domain of ``host``, or None when it has none.

    None for a bare public suffix (``co.uk``, ``azurewebsites.net``) and for anything
    the list cannot place. Unknown TLDs follow the PSL's default ``*`` rule, so
    ``ir.issuer.example`` → ``issuer.example``.
    """
    h = _clean(host)
    if h is None:
        return None
    try:
        domain = _psl().privatesuffix(h)
    except Exception:  # noqa: BLE001
        return None
    return str(domain) if domain else None


def is_public_suffix(host: str | None) -> bool:
    """True when ``host`` is itself a public suffix (``com``, ``co.uk``, ``github.io``)."""
    h = _clean(host)
    return h is not None and public_suffix(h) == h


__all__ = ["is_public_suffix", "public_suffix", "registrable_domain"]
