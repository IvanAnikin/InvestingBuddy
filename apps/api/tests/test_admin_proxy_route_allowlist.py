"""
Contract test: every mounted backend route is reachable through the admin proxy.

The Next.js admin proxy (apps/web/src/app/api/admin/proxy/[...path]/route.ts)
forwards a browser request to FastAPI ONLY when the resolved backend path starts
with one of its ``ALLOWED_PREFIXES`` — otherwise it answers 404 itself and the
backend is never contacted.

That allowlist is a hand-maintained copy of the router layout in
``app/main.py``, so it silently rots: adding a router without adding its prefix
makes the whole feature return a hardcoded 404 that looks exactly like "the
backend has no such endpoint". This is not hypothetical — the Deep Field Review
router (``/api/v1/discovery-runs``) shipped without an entry and every call from
the admin page 404'd at the proxy. ``/api/v1/discovery`` did NOT cover it,
because matching is on a full path segment: the character after "discovery" is
"-", not "/".

This test locks the invariant in the direction that actually breaks: EVERY route
the API mounts must be allowed by the proxy. (The reverse direction is
deliberately not asserted — a stale extra prefix is harmless, it only reaches a
backend 404.)

No network, no credentials; it reads the checked-in route file as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.main import app

PROXY_ROUTE_FILE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "web"
    / "src"
    / "app"
    / "api"
    / "admin"
    / "proxy"
    / "[...path]"
    / "route.ts"
)

# Paths the browser never proxies: FastAPI's own docs/schema surface.
_NOT_PROXIED = {"/api/docs", "/api/redoc", "/api/openapi.json", "/openapi.json"}


def _allowed_prefixes() -> list[str]:
    """Parse ALLOWED_PREFIXES out of the Next.js proxy route file."""
    source = PROXY_ROUTE_FILE.read_text(encoding="utf-8")
    match = re.search(
        r"const\s+ALLOWED_PREFIXES\s*=\s*\[(.*?)\]", source, flags=re.DOTALL
    )
    assert match, "ALLOWED_PREFIXES array not found in the admin proxy route file"
    prefixes = re.findall(r'"([^"]+)"', match.group(1))
    assert prefixes, "ALLOWED_PREFIXES parsed as empty"
    return prefixes


def _is_allowed(backend_path: str, prefixes: list[str]) -> bool:
    """Mirror of ``isAllowed()`` in the proxy route — segment-exact matching."""
    return any(
        backend_path == prefix
        or backend_path.startswith(prefix + "/")
        or backend_path.startswith(prefix + "?")
        for prefix in prefixes
    )


def _mounted_paths() -> list[str]:
    """Every API path the app actually serves, with its full router prefix.

    Read from the generated OpenAPI schema rather than ``app.routes``: recent
    FastAPI versions keep included routers nested, so walking ``app.routes``
    would silently see zero endpoints and the test would pass vacuously.
    """
    paths = set(app.openapi().get("paths", {}))
    assert len(paths) > 10, "OpenAPI schema exposed no paths — the test is vacuous"
    return sorted(p for p in paths if p not in _NOT_PROXIED)


pytestmark = pytest.mark.skipif(
    not PROXY_ROUTE_FILE.exists(),
    reason="apps/web is not present in this checkout (API-only deployment)",
)


def test_every_mounted_api_route_is_reachable_through_the_admin_proxy() -> None:
    prefixes = _allowed_prefixes()
    orphans = [p for p in _mounted_paths() if not _is_allowed(p, prefixes)]
    assert not orphans, (
        "These backend routes are mounted but NOT allowed by the admin proxy — "
        "the proxy would answer 404 without ever contacting the backend. Add "
        "their prefix to ALLOWED_PREFIXES in "
        "apps/web/src/app/api/admin/proxy/[...path]/route.ts: " + ", ".join(orphans)
    )


def test_deep_field_review_paths_are_allowed() -> None:
    """The exact regression: the Deep Field Review router's own paths."""
    prefixes = _allowed_prefixes()
    for path in (
        "/api/v1/discovery-runs/2f1c4a3e-0000-4000-8000-000000000000/field-review",
        "/api/v1/discovery-runs/2f1c4a3e-0000-4000-8000-000000000000"
        "/field-review-eligibility",
    ):
        assert _is_allowed(path, prefixes), path


def test_a_sibling_prefix_does_not_cover_a_hyphenated_route() -> None:
    """Why the bug happened: "/api/v1/discovery" is NOT a prefix match here."""
    assert not _is_allowed("/api/v1/discovery-runs/x/field-review", ["/api/v1/discovery"])


def test_unknown_paths_stay_blocked() -> None:
    """The allowlist must still refuse anything the API does not mount."""
    prefixes = _allowed_prefixes()
    for path in ("/api/v1/secrets", "/internal/admin", "/"):
        assert not _is_allowed(path, prefixes), path
