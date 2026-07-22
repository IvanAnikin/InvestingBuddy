"""
Safe structured request/response logging (Phase 27.1D).

Installs an HTTP middleware that logs ONE structured line per request:

    http_request method=GET path=/api/v1/... status=200 duration_ms=12.3 \
        request_id=... route_family=market-discovery

What is logged: method, path (path ONLY — never the query string, so a token in
a query never lands in a log), response status, duration in ms, a correlation
id, and a coarse route family.

What is NEVER logged: request or response headers (Authorization, Cookie, …),
request or response bodies, query strings, or any credential. The middleware
reads none of them.

The correlation id is taken from an inbound ``X-Request-ID`` header when present
(so a value set by the Next.js proxy / Azure front end is preserved) otherwise a
fresh UUID is generated. It is echoed back on the response ``X-Request-ID``
header and stored on ``request.state.request_id`` for downstream handlers.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response

from app.core.structured_logging import log_event

logger = logging.getLogger("app.request")

_REQUEST_ID_HEADER = "X-Request-ID"


def _is_version_segment(segment: str) -> bool:
    """True for a URL version prefix like ``v1`` / ``v2``."""
    return len(segment) >= 2 and segment[0] == "v" and segment[1:].isdigit()


def route_family(path: str) -> str:
    """Return a coarse, low-cardinality route family for ``path``.

    ``/health`` → ``health``; ``/api/v1/market-discovery/runs/<id>`` →
    ``market-discovery``; ``/`` → ``root``. Never includes ids or query strings,
    so it is safe and stable to group by.
    """
    stripped = path.strip("/")
    if not stripped:
        return "root"
    segments = stripped.split("/")
    # Drop the /api/v1 (or /api) versioning prefix so the family is the resource.
    while segments and (segments[0] == "api" or _is_version_segment(segments[0])):
        segments = segments[1:]
    return segments[0] if segments else "api"


def install_request_logging(app: FastAPI) -> None:
    """Attach the request-logging middleware to ``app`` (outermost when added last)."""

    @app.middleware("http")
    async def _log_requests(request: Request, call_next: object) -> Response:
        # Honour a caller-supplied correlation id; otherwise mint one.
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)  # type: ignore[operator]
            status_code = response.status_code
        except Exception:
            # Log the failed request, then re-raise so error handling is unchanged.
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log_event(
                logger,
                "http_request",
                level=logging.ERROR,
                method=request.method,
                path=request.url.path,  # path only — no query string
                status=500,
                duration_ms=duration_ms,
                request_id=request_id,
                route_family=route_family(request.url.path),
                error="unhandled_exception",
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[_REQUEST_ID_HEADER] = request_id
        log_event(
            logger,
            "http_request",
            level=logging.WARNING if status_code >= 500 else logging.INFO,
            method=request.method,
            path=request.url.path,  # path only — no query string
            status=status_code,
            duration_ms=duration_ms,
            request_id=request_id,
            route_family=route_family(request.url.path),
        )
        return response
