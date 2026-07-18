"""Staging server-to-server access control (Basic Auth).

Extracted into a factory (Phase 23 — Admin/Auth Hardening) so it can be unit
tested in isolation. This remains the backend's server-to-server defense: the
Next.js admin proxy authenticates the human admin and only then attaches this
Basic Auth credential. The backend never trusts the forwarded admin identity
headers as an authentication signal — see ``app.core.admin_identity``.
"""

from __future__ import annotations

import base64
import hmac
import logging

from fastapi import FastAPI, Request, Response

from app.core.admin_identity import admin_identity_from_headers

_audit_logger = logging.getLogger("app.admin_audit")


def install_staging_basic_auth(app: FastAPI, credentials: str) -> None:
    """Protect all routes (except /health) with HTTP Basic Auth.

    ``credentials`` is a ``"username:password"`` string. Requests that do not
    present exactly this credential receive a 401. Only after a request passes
    Basic Auth are the advisory ``X-IB-Admin-*`` identity headers read and
    surfaced on ``request.state`` (and logged for mutating actions).
    """
    expected = base64.b64encode(credentials.encode()).decode()

    @app.middleware("http")
    async def staging_basic_auth(request: Request, call_next: object) -> Response:
        if request.url.path == "/health":
            return await call_next(request)  # type: ignore[operator]
        auth = request.headers.get("Authorization", "")
        if hmac.compare_digest(auth, f"Basic {expected}"):
            # Basic Auth passed — now (and only now) read the advisory admin
            # identity forwarded by the proxy. Never trusted for auth.
            identity = admin_identity_from_headers(
                request.headers, authenticated=True
            )
            request.state.admin_email = identity.email
            request.state.admin_name = identity.name
            if identity.is_present and request.method not in ("GET", "HEAD"):
                _audit_logger.info(
                    "admin_action user=%s method=%s path=%s",
                    identity.email,
                    request.method,
                    request.url.path,
                )
            return await call_next(request)  # type: ignore[operator]
        return Response(
            content="Staging access restricted",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="InvestingBuddy Staging"'},
        )
