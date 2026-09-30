"""Canonical URLs for open-web fetches — open-web W2 (spec §9.2 "Canonicalisation").

The canonical URL is an IDENTITY: the key for the negative cache, for de-duplication
and (from W3) for document versions. It is never the URL that is requested. W0's D12
lesson applies in full — stripping a parameter from a URL makes a different request (a
signed CDN link without its signature, say), so :func:`canonical_url` feeds storage and
cache keys only, and the fetcher keeps requesting the URL exactly as it was given.

Canonical form = W0's :func:`canonicalize_source_url` (credential-like query parameters,
userinfo and the fragment dropped; scheme and host lower-cased; path escapes normalised)
plus:

* tracking parameters removed: ``utm_*``, ``fbclid``, ``gclid``, ``mc_cid``, ``mc_eid``,
  ``_hsenc``, ``_hsmi``, ``ref``, ``ref_src``, ``cmpid``, ``ocid``, ``igshid``;
* the default port ``:443`` dropped for ``https``.

``rel=canonical`` (an HTML ``<link>`` or an HTTP ``Link`` header) is honoured only when it
points into the SAME registrable domain as the page that declared it. A page cannot
claim to be some other site's document. The declared target is recorded either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from app.services.sources.public_suffix import registrable_domain
from app.services.sources.redaction import canonicalize_source_url, strip_url_secrets
from app.services.sources.safe_web_fetcher import check_url_shape, normalize_link_url
from app.services.web_research.content import iter_start_tags

#: Exact tracking-parameter names (case-insensitive). ``utm_*`` is a prefix rule.
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "ref",
        "ref_src",
        "cmpid",
        "ocid",
        "igshid",
    }
)
TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)

_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
#: One ``<url>; params`` entry of an HTTP ``Link`` header.
_LINK_HEADER_RE = re.compile(r"<([^>]*)>\s*((?:;[^,]*)*)")
#: Only the head of a page is scanned for ``<link rel=canonical>``.
_CANONICAL_SCAN_CHARS = 262_144


def is_tracking_param(name: str) -> bool:
    lowered = (name or "").strip().lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def stored_url(url: str | None) -> str | None:
    """The W0 stored form of any URL we record: canonical and credential-free.

    Used for ``requested_url``, ``final_url`` and every redirect hop. Tracking
    parameters are KEPT here (the stored form says what was requested); they are
    removed only in :func:`canonical_url`.
    """
    if not url:
        return url
    return canonicalize_source_url(strip_url_secrets(url))


def canonical_url(url: str | None) -> str | None:
    """The identity of ``url``: stored form minus tracking parameters and ``:443``."""
    base = stored_url(url)
    if not base:
        return base
    try:
        parts = urlsplit(base)
    except (ValueError, TypeError):
        return base
    netloc = parts.netloc
    if parts.scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]
    query = parts.query
    if query:
        kept = [
            (name, value)
            for name, value in parse_qsl(query, keep_blank_values=True)
            if not is_tracking_param(name)
        ]
        query = urlencode(kept)
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        name = m.group(1).lower()
        if name not in out:
            out[name] = m.group(2) or m.group(3) or m.group(4) or ""
    return out


def rel_canonical_from_html(html: str | None) -> str | None:
    """The first ``<link rel="canonical" href="…">`` in the page head, raw, or None."""
    if not html:
        return None
    for tag in iter_start_tags(html, "link", max_chars=_CANONICAL_SCAN_CHARS):
        attrs = _attrs(tag)
        rels = {r.strip().lower() for r in attrs.get("rel", "").split()}
        if "canonical" in rels and attrs.get("href", "").strip():
            return attrs["href"].strip()
    return None


def rel_canonical_from_header(link_header: str | None) -> str | None:
    """The target of a ``Link: <…>; rel="canonical"`` header entry, raw, or None."""
    if not link_header:
        return None
    for m in _LINK_HEADER_RE.finditer(link_header):
        params = m.group(2) or ""
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "rel":
                rels = {r.strip().lower() for r in value.strip().strip('"').split()}
                if "canonical" in rels and m.group(1).strip():
                    return m.group(1).strip()
    return None


@dataclass(frozen=True)
class CanonicalDecision:
    """The canonical URL chosen for one fetched page, and why."""

    #: The identity used for caches and (W3) document versions.
    canonical_url: str | None
    #: What the page declared (stored form), honoured or not. None when it declared none.
    declared: str | None = None
    #: True when the declared target was adopted as ``canonical_url``.
    honoured: bool = False
    #: ``none`` | ``honoured`` | ``cross_domain`` | ``invalid``.
    reason: str = "none"


def choose_canonical(fetched_url: str, declared_raw: str | None) -> CanonicalDecision:
    """Pick the canonical URL for a page fetched at ``fetched_url``.

    ``declared_raw`` (from :func:`rel_canonical_from_html` / ``_from_header``) is
    resolved against ``fetched_url`` and adopted only when it is an https URL of an
    acceptable shape on the SAME registrable domain. Otherwise the fetched URL's own
    canonical form stands and the declaration is recorded with the reason it was not
    honoured.
    """
    own = canonical_url(fetched_url)
    if not declared_raw:
        return CanonicalDecision(canonical_url=own)
    resolved = normalize_link_url(urljoin(fetched_url, declared_raw)) or ""
    declared_stored = stored_url(resolved) if resolved else None
    reason, host = check_url_shape(resolved)
    if reason or not host:
        return CanonicalDecision(own, declared_stored, False, "invalid")
    own_host = (urlsplit(fetched_url).hostname or "").lower()
    own_domain = registrable_domain(own_host) or own_host
    target_domain = registrable_domain(host) or host
    if own_domain != target_domain:
        return CanonicalDecision(own, declared_stored, False, "cross_domain")
    return CanonicalDecision(canonical_url(resolved), declared_stored, True, "honoured")


__all__ = [
    "TRACKING_PARAMS",
    "TRACKING_PREFIXES",
    "CanonicalDecision",
    "canonical_url",
    "choose_canonical",
    "is_tracking_param",
    "rel_canonical_from_header",
    "rel_canonical_from_html",
    "stored_url",
]
