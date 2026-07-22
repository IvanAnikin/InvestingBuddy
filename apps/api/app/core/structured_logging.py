"""
Structured event logging (Phase 27.1D — Staging Telemetry / Logging Cleanup).

Emits log lines in a stable ``event key=value key=value`` shape so a staging
operator can grep the container log stream for a named event
(``discovery_run_started``, ``report_validation`` …) and read its fields without
a log-aggregation backend.

Safety:
  * Every field is passed through :func:`app.core.log_redaction.is_sensitive_key`
    so a field accidentally named like a secret is redacted before it is
    formatted — the value never reaches the log.
  * ``None`` fields are dropped to keep lines compact.
  * Values are coerced to ``str`` and stripped of newlines so a single event is
    always a single log line (no log-forging via embedded newlines).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.log_redaction import REDACTED, is_sensitive_key


def _format_value(value: Any) -> str:
    """Coerce a field value to a compact, single-line string."""
    text = str(value)
    # Collapse any newlines/tabs so one event is always one log line.
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # Quote values containing spaces so key=value parsing stays unambiguous.
    if " " in text or "=" in text:
        text = f'"{text}"'
    return text


def format_event(event: str, fields: dict[str, Any]) -> str:
    """Return a ``event key=value ...`` string with sensitive keys redacted."""
    parts: list[str] = [event]
    for key, value in fields.items():
        if value is None:
            continue
        safe = REDACTED if is_sensitive_key(str(key)) else _format_value(value)
        parts.append(f"{key}={safe}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a single structured event line at ``level`` on ``logger``.

    Example::

        log_event(logger, "discovery_run_started", run_id=rid, mode="thesis")
        # → "discovery_run_started run_id=... mode=thesis"
    """
    logger.log(level, format_event(event, fields))
