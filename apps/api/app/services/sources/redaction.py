"""
Redaction helpers for the source framework — Phase 29A.

Stored evidence is not a log line: we would rather *drop* a credential-bearing
query parameter from a URL entirely than keep ``?api_token=***REDACTED***``
around. This module builds on the single tested source of truth in
``app.core.log_redaction`` and adds ``strip_url_secrets`` for that purpose.

Nothing here ever raises — redaction must never break the path it protects.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.log_redaction import (
    redact_text,
    redact_url,
)

__all__ = [
    "canonicalize_source_url",
    "redact_text",
    "redact_url",
    "strip_url_secrets",
    "url_has_secret",
]


#: Whole WORDS of a parameter name that mark it credential-bearing. W0 / D12: this was
#: substring matching, so ``countrycode``, ``sortkey`` and ``design`` (which contain
#: ``code``/``key``/``sig``) were stripped — and the stripped URL was the one fetched.
_SECRET_PARAM_WORDS = frozenset(
    {
        "token",
        "key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "sig",
        "signature",
        "code",
        "auth",
        "credential",
        "credentials",
    }
)
#: Compound names written as one lower-case word (``apitoken``, ``clientsecret``,
#: ``xamzsignature``) end in one of these. Deliberately NOT ``key``/``code``/``sig``,
#: whose suffix match is exactly the false positive D12 is about.
_SECRET_PARAM_SUFFIXES = ("token", "secret", "password", "passwd", "signature", "apikey")

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _param_words(name: str) -> list[str]:
    """``X-Amz-Signature`` → ``[x, amz, signature]``; ``apiKey`` → ``[api, key]``."""
    spaced = _CAMEL_BOUNDARY_RE.sub("_", name or "").lower()
    return [word for word in _WORD_SPLIT_RE.split(spaced) if word]


def _is_secret_param(name: str) -> bool:
    """True when a query parameter NAME is credential-bearing (whole-word match)."""
    words = _param_words(name)
    if any(word in _SECRET_PARAM_WORDS for word in words):
        return True
    joined = "".join(words)
    return bool(joined) and joined.endswith(_SECRET_PARAM_SUFFIXES)


def strip_url_secrets(url: str | None) -> str | None:
    """Return ``url`` with every credential-bearing query parameter removed.

    For the STORED / LOGGED form of a URL only. Never feed the result back into a
    fetch: a URL without its signature is a different request (W0 / D12).

    Scheme, host and path are preserved. Unlike ``redact_url`` (which keeps the
    key and hides the value for log readability), this drops the whole parameter
    so persisted evidence carries no residue of a token at all. A URL that cannot
    be parsed, or has no query string, is returned unchanged.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        kept = [
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_secret_param(name)
        ]
        new_query = urlencode(kept)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except (ValueError, TypeError):
        return url


_PCT_ESCAPE_RE = re.compile(r"%([0-9a-fA-F]{2})")


def _normalize_path_encoding(path: str) -> str:
    """Encode literal spaces and upper-case existing escapes. Never decodes."""
    if not path:
        return path
    return _PCT_ESCAPE_RE.sub(
        lambda m: "%" + m.group(1).upper(), path.replace(" ", "%20")
    )


def canonicalize_source_url(url: str | None) -> str | None:
    """Return a canonical, credential-free form of ``url`` for persistence.

    ``strip_url_secrets`` only removes credential-bearing QUERY parameters; it
    leaves ``user:pass@`` userinfo and the ``#fragment`` intact (Phase 32A Slice
    3 security review). This canonicaliser goes further so no credential-like
    residue is ever persisted on a Source/Citation:

      - drops every credential-bearing query parameter (via ``strip_url_secrets``);
      - strips ``user:pass@`` userinfo from the authority;
      - lower-cases the scheme + host (path/query case is preserved);
      - drops the fragment;
      - canonicalises PATH PERCENT-ENCODING (a literal space becomes ``%20``;
        existing escapes are upper-cased). Encoding-only, never decoding, so
        ``a%2Fb`` is never conflated with ``a/b``. Without this the SAME
        document reachable as ``Annual Report 2025`` and
        ``Annual%20Report%202025`` produced two identities, two fetch attempts
        and two cache entries.

    Never raises: an unpar't URL is returned as the secret-stripped best effort.
    """
    if not url:
        return url
    cleaned = strip_url_secrets(url)
    if not cleaned:
        return cleaned
    try:
        parts = urlsplit(cleaned)
        host = (parts.hostname or "").lower()
        if host:
            netloc = host
            if parts.port is not None:
                netloc = f"{host}:{parts.port}"
        else:
            # No parseable host (e.g. a scheme-less path) — drop any userinfo
            # defensively and keep the rest.
            netloc = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit(
            (
                parts.scheme.lower(),
                netloc,
                _normalize_path_encoding(parts.path),
                parts.query,
                "",
            )
        )
    except (ValueError, TypeError):
        return cleaned


def url_has_secret(url: str | None) -> bool:
    """True when ``url`` carries a credential-bearing query parameter."""
    if not url:
        return False
    try:
        parts = urlsplit(url)
        if not parts.query:
            return False
        return any(
            _is_secret_param(name)
            for name, _ in parse_qsl(parts.query, keep_blank_values=True)
        )
    except (ValueError, TypeError):
        return False
