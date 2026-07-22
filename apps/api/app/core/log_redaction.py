"""
Log redaction helpers (Phase 27.1D — Staging Telemetry / Logging Cleanup).

A single, tested source of truth for stripping secrets out of anything that is
about to be logged. The logging code in this codebase never logs request/response
bodies or headers wholesale, but this module exists so that if a value ever DOES
flow toward a log line, the sensitive parts are neutralised first.

Design rules:
  * Redaction is by KEY NAME, case-insensitive, substring match. A header or
    mapping key whose name contains any sensitive token is replaced with
    ``REDACTED`` — the value is never emitted, not even partially.
  * URLs are redacted by query-parameter name (tokens, keys, signatures) so a
    ``?api_token=...`` never lands in a log while the path stays readable.
  * Nothing here raises: redaction must never break the code path it protects.

This module has NO dependencies on FastAPI/Starlette so it stays trivially
unit-testable and reusable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***REDACTED***"

# Header names that must never appear in a log line (case-insensitive, exact).
SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-ib-admin-token",
    }
)

# Substrings that mark a mapping/query key as sensitive (case-insensitive).
# Matching is substring-based so ``eodhd_api_key`` and ``AUTH_GITHUB_SECRET``
# are both caught without enumerating every variant.
SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "auth_token",
    "cookie",
    "credential",
    "database_url",
    "connection_string",
    "basic_auth",
    "private_key",
    "signature",
    "session",
)

# Query-parameter names (or substrings) whose VALUE is a secret in a URL.
SENSITIVE_QUERY_SUBSTRINGS: tuple[str, ...] = (
    "token",
    "api_key",
    "apikey",
    "api-key",
    "key",
    "secret",
    "password",
    "sig",
    "signature",
    "code",
    "access_token",
    "auth",
)


def is_sensitive_key(name: str) -> bool:
    """True if a mapping/header key name should have its value redacted."""
    if not name:
        return False
    lowered = name.lower()
    if lowered in SENSITIVE_HEADERS:
        return True
    return any(token in lowered for token in SENSITIVE_KEY_SUBSTRINGS)


def redact_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return a copy of ``headers`` with every sensitive header value redacted.

    Accepts a mapping or an iterable of (name, value) pairs (Starlette's
    ``request.headers.items()``). Header names are preserved so a log reader can
    still see WHICH header was present, only the value is hidden.
    """
    items: Iterable[tuple[str, str]]
    if isinstance(headers, Mapping):
        items = headers.items()
    else:
        items = headers

    out: dict[str, str] = {}
    for name, value in items:
        lowered = str(name).lower()
        if lowered in SENSITIVE_HEADERS or is_sensitive_key(lowered):
            out[str(name)] = REDACTED
        else:
            out[str(name)] = str(value)
    return out


def redact_value(key: str, value: Any) -> Any:
    """Redact ``value`` if ``key`` names a sensitive field, else return it."""
    return REDACTED if is_sensitive_key(key) else value


def redact_mapping(data: Mapping[str, Any], *, _depth: int = 0) -> dict[str, Any]:
    """Return a copy of ``data`` with sensitive keys redacted (recursively).

    Nested mappings are walked so a secret buried under a non-sensitive parent
    key is still caught. Recursion is depth-bounded so a pathological structure
    can never hang the logger.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(str(key)):
            out[key] = REDACTED
        elif isinstance(value, Mapping) and _depth < 6:
            out[key] = redact_mapping(value, _depth=_depth + 1)
        else:
            out[key] = value
    return out


def redact_url(url: str) -> str:
    """Return ``url`` with the values of any token-bearing query params redacted.

    The scheme, host and path are preserved so the log stays useful; only the
    values of query parameters whose name looks like a credential are replaced.
    Never raises — a URL that cannot be parsed is returned unchanged.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        redacted_pairs: list[tuple[str, str]] = []
        for name, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = name.lower()
            if any(token in lowered for token in SENSITIVE_QUERY_SUBSTRINGS):
                redacted_pairs.append((name, REDACTED))
            else:
                redacted_pairs.append((name, value))
        new_query = urlencode(redacted_pairs)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except (ValueError, TypeError):
        return url
