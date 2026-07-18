"""Phase 23 — Admin/Auth Hardening (backend defense-in-depth).

Covers:
  - Basic Auth still protects backend admin endpoints (missing/invalid rejected,
    valid accepted, /health always open).
  - The advisory X-IB-Admin-Email header is surfaced only AFTER Basic Auth
    passes, and is never trusted without it (a spoofed header on an
    unauthenticated request is rejected before any handler runs).
  - Header sanitization strips control characters / caps length.

The staging Basic Auth middleware is exercised on a freshly-built app via the
``install_staging_basic_auth`` factory, so these tests do not depend on the
process-wide APP_ENV.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.admin_identity import (
    ADMIN_EMAIL_HEADER,
    ADMIN_NAME_HEADER,
    admin_identity_from_headers,
    sanitize,
)
from app.core.staging_auth import install_staging_basic_auth

CREDENTIALS = "ibadmin:s3cret"
BASIC = "Basic " + base64.b64encode(CREDENTIALS.encode()).decode()


def _build_app() -> FastAPI:
    app = FastAPI()
    install_staging_basic_auth(app, CREDENTIALS)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/reports")
    async def protected(request: Request) -> dict:
        # Echo whatever admin identity the middleware surfaced (or None).
        return {"admin_email": getattr(request.state, "admin_email", None)}

    return app


@pytest.fixture
async def staging_client() -> AsyncClient:
    app = _build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Basic Auth gate ────────────────────────────────────────────────────────


async def test_health_bypasses_basic_auth(staging_client: AsyncClient) -> None:
    res = await staging_client.get("/health")
    assert res.status_code == 200


async def test_protected_endpoint_rejects_missing_auth(
    staging_client: AsyncClient,
) -> None:
    res = await staging_client.post("/api/v1/reports")
    assert res.status_code == 401
    assert res.headers.get("WWW-Authenticate", "").startswith("Basic")


async def test_protected_endpoint_rejects_invalid_auth(
    staging_client: AsyncClient,
) -> None:
    bad = "Basic " + base64.b64encode(b"ibadmin:wrong").decode()
    res = await staging_client.post(
        "/api/v1/reports", headers={"Authorization": bad}
    )
    assert res.status_code == 401


async def test_protected_endpoint_accepts_valid_auth(
    staging_client: AsyncClient,
) -> None:
    res = await staging_client.post(
        "/api/v1/reports", headers={"Authorization": BASIC}
    )
    assert res.status_code == 200


# ── Admin identity header only trusted after Basic Auth ────────────────────


async def test_admin_email_header_surfaced_with_valid_auth(
    staging_client: AsyncClient,
) -> None:
    res = await staging_client.post(
        "/api/v1/reports",
        headers={"Authorization": BASIC, ADMIN_EMAIL_HEADER: "admin@example.com"},
    )
    assert res.status_code == 200
    assert res.json()["admin_email"] == "admin@example.com"


async def test_spoofed_admin_header_without_auth_is_rejected(
    staging_client: AsyncClient,
) -> None:
    # No Basic Auth, but a spoofed identity header — must be rejected outright,
    # so the header can never influence behaviour without authentication.
    res = await staging_client.post(
        "/api/v1/reports",
        headers={ADMIN_EMAIL_HEADER: "attacker@evil.example"},
    )
    assert res.status_code == 401


# ── Pure helper behaviour ──────────────────────────────────────────────────


def test_admin_identity_not_trusted_when_unauthenticated() -> None:
    headers = {ADMIN_EMAIL_HEADER: "admin@example.com", ADMIN_NAME_HEADER: "A"}
    identity = admin_identity_from_headers(headers, authenticated=False)
    assert identity.email is None
    assert identity.name is None
    assert identity.is_present is False


def test_admin_identity_extracted_when_authenticated() -> None:
    headers = {ADMIN_EMAIL_HEADER: "admin@example.com", ADMIN_NAME_HEADER: "Ada"}
    identity = admin_identity_from_headers(headers, authenticated=True)
    assert identity.email == "admin@example.com"
    assert identity.name == "Ada"
    assert identity.is_present is True


def test_sanitize_strips_control_chars_and_caps_length() -> None:
    assert sanitize("a\r\nb\x00c") == "abc"
    assert sanitize("  spaced  ") == "spaced"
    assert sanitize("") is None
    assert sanitize(None) is None
    long = "x" * 500
    assert sanitize(long) is not None
    assert len(sanitize(long)) == 320  # type: ignore[arg-type]
