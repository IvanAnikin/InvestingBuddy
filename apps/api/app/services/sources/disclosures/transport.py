"""One guarded GET to a FIXED official API host — shared by EDINET and OpenDART.

The host is a constant of the calling module, never taken from data. The request is
SSRF/DNS-checked and pinned, redirects are refused, the body is bounded, and failures
come back as CODES: an exception's text can carry the request URL, and OpenDART's key
can only travel as a query parameter, so no exception text ever leaves this function.
Query parameters (which may hold a key) are passed separately and never logged.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def guarded_get(
    url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None,
    cfg: Any, max_bytes: int, allowed_host: str, timeout_seconds: float | None = None,
) -> tuple[int | None, bytes | None, str | None]:
    """One GET to a FIXED official host: SSRF/DNS-guarded, pinned, no redirects followed,
    body bounded. Never raises; errors are codes, never provider text or a URL.

    ``timeout_seconds`` is the whole request's deadline (default: twice
    ``source_document_extraction_timeout_seconds``) — for a host measured to be slow."""
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent
    from app.services.sources.safe_web_fetcher import (
        async_check_fetch_url,
        guarded_client_kwargs,
        host_of,
        pinned_transport_for,
        read_bounded_body,
    )

    if host_of(url) != allowed_host:
        return None, None, "host_not_allowed"
    budget = float(getattr(cfg, "source_document_extraction_timeout_seconds", 60) or 60)
    total = budget * 2
    if timeout_seconds:
        budget = total = float(timeout_seconds)
    try:
        async with asyncio.timeout(total):
            reason, pinned_ip = await async_check_fetch_url(
                url, (allowed_host,), cfg=cfg, resolve_ip=True)
            if reason:
                return None, None, "blocked"
            sent = {USER_AGENT_HEADER: research_user_agent(cfg), "Accept": "*/*"}
            sent.update(headers or {})
            kwargs = guarded_client_kwargs(
                timeout=budget, headers=sent,
                transport=pinned_transport_for(cfg, allowed_host, pinned_ip))
            async with httpx.AsyncClient(**kwargs) as client, client.stream(
                "GET", url, params=params or None
            ) as resp:
                if resp.is_redirect:
                    return resp.status_code, None, "redirect_refused"
                if resp.status_code >= 400:
                    return resp.status_code, None, f"http_{resp.status_code}"
                body = await read_bounded_body(resp, max_bytes=max_bytes)
                if body.error or body.truncated:
                    return resp.status_code, None, body.error or "too_large"
                return resp.status_code, body.content, None
    except TimeoutError:
        return None, None, "timeout"
    except Exception as exc:  # noqa: BLE001 - the exception text can carry the URL
        return None, None, type(exc).__name__


__all__ = ["guarded_get"]
