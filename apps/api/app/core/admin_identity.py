"""Admin identity audit headers (Phase 23 — Admin/Auth Hardening).

The Next.js admin proxy authenticates and authorizes the admin user, then
forwards two *non-sensitive* identity headers to the backend for audit logging:

    X-IB-Admin-Email
    X-IB-Admin-Name

These headers are advisory only. They are **never** trusted as an authentication
signal: the backend continues to require its own Basic Auth (see
``app.main``), and this module refuses to surface any identity unless the caller
has already been authenticated by that Basic Auth layer. A client that spoofs
the header without valid Basic Auth is rejected before any handler runs, so the
header can never influence behaviour on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

ADMIN_EMAIL_HEADER = "X-IB-Admin-Email"
ADMIN_NAME_HEADER = "X-IB-Admin-Name"

_MAX_LEN = 320


def sanitize(value: str | None) -> str | None:
    """Strip control characters and cap length. Returns None when empty."""
    if not value:
        return None
    cleaned = "".join(ch for ch in value if 0x20 <= ord(ch) <= 0x7E).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_LEN]


@dataclass(frozen=True)
class AdminIdentity:
    email: str | None
    name: str | None

    @property
    def is_present(self) -> bool:
        return self.email is not None


def admin_identity_from_headers(
    headers: object,
    *,
    authenticated: bool,
) -> AdminIdentity:
    """Extract the advisory admin identity from request headers.

    Returns an empty identity unless ``authenticated`` is True — the backend
    never relies on a spoofable header without valid Basic Auth. ``headers`` is
    any mapping-like object exposing ``.get(name)`` (e.g. Starlette Headers).
    """
    if not authenticated:
        return AdminIdentity(email=None, name=None)
    get = getattr(headers, "get", None)
    if get is None:
        return AdminIdentity(email=None, name=None)
    return AdminIdentity(
        email=sanitize(get(ADMIN_EMAIL_HEADER)),
        name=sanitize(get(ADMIN_NAME_HEADER)),
    )
