"""
Application logging configuration (Phase 27.1D — Staging Telemetry / Logging).

Why this exists: under gunicorn on Azure App Service the *root* logger is not
configured by default, so application ``logging.info(...)`` calls fall through to
Python's "handler of last resort" which only emits WARNING and above to stderr.
That is exactly why INFO-level structured events were invisible in the staging
container log stream. :func:`configure_logging` guarantees a stdout handler at
the configured level so those events reach ``containerStream.log``.

Kept intentionally small and dependency-free (only stdlib + settings). It never
logs or configures anything secret — only a level and an output stream.
"""

from __future__ import annotations

import logging
import sys

# Name tag on our handler so configuration is idempotent — we never attach a
# second copy, and we never clobber a gunicorn/uvicorn handler that is already
# present.
_HANDLER_NAME = "investingbuddy-stdout"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def _resolve_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    if not level:
        return logging.INFO
    return logging.getLevelName(str(level).upper()) if isinstance(level, str) else logging.INFO


def configure_logging(level: str | int | None = None) -> None:
    """Ensure app logs reach stdout at ``level`` (default from settings/INFO).

    Idempotent: a second call does not add a duplicate handler. Existing
    gunicorn/uvicorn handlers are left in place; we only guarantee our own
    stdout handler exists so INFO events are never dropped.
    """
    # Imported lazily so importing this module never forces settings resolution
    # at import time (keeps unit tests that stub settings simple).
    from app.core.config import settings

    resolved = _resolve_level(level if level is not None else settings.log_level)
    if not isinstance(resolved, int):  # getLevelName may return a str for junk input
        resolved = logging.INFO

    root = logging.getLogger()
    root.setLevel(resolved)

    already = any(getattr(h, "name", "") == _HANDLER_NAME for h in root.handlers)
    if not already:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.name = _HANDLER_NAME
        stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        stream_handler.setLevel(resolved)
        root.addHandler(stream_handler)
    else:
        for existing in root.handlers:
            if getattr(existing, "name", "") == _HANDLER_NAME:
                existing.setLevel(resolved)
