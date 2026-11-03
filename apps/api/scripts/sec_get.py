#!/usr/bin/env python
"""
sec_get.py — the ONE sanctioned way for an operator or agent script to read sec.gov.

WHY (ADR-081)
=============
During the 2026-11 QA campaign an ad-hoc script sent requests to sec.gov with a
developer's PERSONAL email address as its User-Agent, twice. The product code never
did; the scripts improvised because nothing sanctioned existed. This helper is that
sanctioned path: it sends exactly the platform's research User-Agent
(``app.core.outbound_identity.research_user_agent``), whose contact is a validated
SERVICE mailbox, and it keeps the platform's SEC request spacing.

USAGE
=====
    cd apps/api
    .venv/bin/python scripts/sec_get.py URL [OUT]

* ``URL`` must be ``https://www.sec.gov/...``, ``https://data.sec.gov/...`` or
  ``https://efts.sec.gov/...``. Anything else is refused.
* ``OUT`` is a file to write the body to; without it the body goes to stdout.
* There is NO way to change the User-Agent. Any option (``--user-agent``, ``-A``,
  ``-H``, ...) or any ``*USER_AGENT*`` environment variable makes the helper refuse
  to run. The contact comes only from the ``OUTBOUND_CONTACT`` setting, and that
  setting refuses consumer-mail and local-identity addresses (fail closed).
* Requests are spaced by ``SEC_REQUEST_MIN_INTERVAL_MS`` — across invocations too,
  through a timestamp file in the system temp directory.
* Redirects are not followed; the body is capped; no proxy from the environment.

Exit codes: 0 fetched; 2 refused (bad URL / override supplied); 3 HTTP or
transport failure. Nothing it prints contains the User-Agent or the contact.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent  # noqa: E402

ALLOWED_HOSTS: frozenset[str] = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})
MAX_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
STATE_FILE_NAME = "investingbuddy-sec-get.last"

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

USAGE = "usage: sec_get.py URL [OUT]   (no options; the User-Agent cannot be overridden)"


def url_refusal_reason(url: str) -> str | None:
    """Why ``url`` may not be fetched by this helper, or ``None`` when it may."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparseable URL"
    if parts.scheme != "https":
        return "only https:// is allowed"
    if parts.username is not None or parts.password is not None:
        return "credentials in the URL are not allowed"
    try:
        port = parts.port
    except ValueError:
        return "invalid port"
    if port not in (None, 443):
        return "a non-default port is not allowed"
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return "host is not www.sec.gov, data.sec.gov or efts.sec.gov"
    return None


def override_refusal_reason(argv: Sequence[str], environ: Mapping[str, str]) -> str | None:
    """Refuse any attempt to supply a User-Agent (or any option at all)."""
    for arg in argv:
        if arg.startswith("-"):
            return "options are not accepted (the User-Agent cannot be overridden)"
    for name in environ:
        if "USER_AGENT" in name.upper() or "USERAGENT" in name.upper():
            return "a *USER_AGENT* environment variable is set; unset it"
    return None


def _min_interval_seconds() -> float:
    try:
        return max(0.0, int(settings.sec_request_min_interval_ms) / 1000.0)
    except (TypeError, ValueError):
        return 0.12


def _wait_for_slot(
    state_path: pathlib.Path,
    *,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Keep the SEC spacing across invocations via a timestamp file."""
    interval = _min_interval_seconds()
    try:
        last = float(state_path.read_text().strip())
    except (OSError, ValueError):
        last = None
    if last is not None:
        wait = interval - (clock() - last)
        if wait > 0:
            sleep(min(wait, interval))
    try:
        state_path.write_text(f"{clock():.6f}")
    except OSError:
        pass


def fetch(url: str, *, transport: Any = None) -> tuple[int, bytes, bool]:
    """GET ``url`` with the central UA. Returns ``(status, body, truncated)``."""
    import httpx

    timeout = getattr(settings, "primary_document_fetch_timeout_seconds", None)
    client_kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "trust_env": False,
        "timeout": max(1, int(timeout or DEFAULT_TIMEOUT_SECONDS)),
        "headers": {USER_AGENT_HEADER: research_user_agent(), "Accept": "*/*"},
    }
    if transport is not None:
        client_kwargs["transport"] = transport
    with httpx.Client(**client_kwargs) as client:
        with client.stream("GET", url) as resp:
            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    truncated = True
                    break
            return resp.status_code, b"".join(chunks)[:MAX_BYTES], truncated


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    state_path: pathlib.Path | None = None,
    stdout: IO[bytes] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    err = stderr or sys.stderr

    reason = override_refusal_reason(args, env)
    if reason is not None:
        print(f"sec_get: refused: {reason}\n{USAGE}", file=err)
        return EXIT_REFUSED
    if not 1 <= len(args) <= 2:
        print(USAGE, file=err)
        return EXIT_REFUSED
    url = args[0]
    reason = url_refusal_reason(url)
    if reason is not None:
        print(f"sec_get: refused: {reason}", file=err)
        return EXIT_REFUSED

    _wait_for_slot(
        state_path or pathlib.Path(tempfile.gettempdir()) / STATE_FILE_NAME,
        sleep=sleep,
        clock=clock,
    )
    try:
        status, body, truncated = fetch(url, transport=transport)
    except Exception as exc:  # noqa: BLE001 - report the class, never a traceback
        print(f"sec_get: transport failure: {type(exc).__name__}", file=err)
        return EXIT_FAILED
    if not 200 <= status < 300:
        print(f"sec_get: HTTP {status} (redirects are not followed)", file=err)
        return EXIT_FAILED
    if truncated:
        print(f"sec_get: body exceeded {MAX_BYTES} bytes; refusing a partial copy", file=err)
        return EXIT_FAILED

    if len(args) == 2:
        pathlib.Path(args[1]).write_bytes(body)
        print(f"sec_get: HTTP {status}, {len(body)} bytes -> {args[1]}", file=err)
    else:
        out = stdout or sys.stdout.buffer
        out.write(body)
        out.flush()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
