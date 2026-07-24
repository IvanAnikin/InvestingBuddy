"""
Connector error taxonomy — Phase 29A.

A connector failure must never crash a report or a discovery run, and must never
leak a secret or a raw upstream error body (which can contain an ``api_token``
in an echoed request URL). ``ConnectorError`` therefore carries a coarse machine
code plus a *pre-redacted* human message. ``to_safe_dict()`` is the only shape
that ever leaves the process.
"""

from __future__ import annotations

from enum import Enum

from app.services.sources.redaction import redact_text


class ConnectorErrorCode(str, Enum):
    not_implemented = "not_implemented"
    not_configured = "not_configured"
    disabled = "disabled"
    rate_limited = "rate_limited"
    timeout = "timeout"
    upstream_error = "upstream_error"
    parse_error = "parse_error"
    unknown = "unknown"


class ConnectorError(Exception):
    """A safe, classified connector failure.

    The ``message`` is redacted at construction so nothing tokenised ever
    survives into logs, API responses, or source gaps.
    """

    def __init__(
        self,
        code: ConnectorErrorCode,
        message: str | None = None,
        *,
        source_id: str | None = None,
        connector_key: str | None = None,
    ) -> None:
        self.code = code
        self.source_id = source_id
        self.connector_key = connector_key
        # Never store a raw upstream body — redact defensively.
        self.safe_message = redact_text(message or code.value)
        super().__init__(self.safe_message)

    def to_safe_dict(self) -> dict[str, str | None]:
        return {
            "error_code": self.code.value,
            "message": self.safe_message,
            "source_id": self.source_id,
            "connector_key": self.connector_key,
        }


__all__ = ["ConnectorErrorCode", "ConnectorError"]
