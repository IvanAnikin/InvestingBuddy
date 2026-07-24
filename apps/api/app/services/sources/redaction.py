"""
Redaction helpers for the source framework — Phase 29A.

Stored evidence is not a log line: we would rather *drop* a credential-bearing
query parameter from a URL entirely than keep ``?api_token=***REDACTED***``
around. This module builds on the single tested source of truth in
``app.core.log_redaction`` and adds ``strip_url_secrets`` for that purpose.

Nothing here ever raises — redaction must never break the path it protects.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.log_redaction import (
    SENSITIVE_QUERY_SUBSTRINGS,
    redact_text,
    redact_url,
)

__all__ = [
    "redact_text",
    "redact_url",
    "strip_url_secrets",
    "url_has_secret",
]


def _is_secret_param(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in SENSITIVE_QUERY_SUBSTRINGS)


def strip_url_secrets(url: str | None) -> str | None:
    """Return ``url`` with every credential-bearing query parameter removed.

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
