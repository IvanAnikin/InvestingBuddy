"""The live World Bank Indicators source — V3.4 Slice 4.7.

WHY THIS ONE FIRST
==================
Free, public, **no credential**, an explicit open licence (CC BY 4.0), a stable
documented JSON contract, and it publishes the annual national accounts a macro question
about a country most often needs. Everything V3 requires of a source is true of it
without anybody buying anything, which is the constraint the whole 2026-09-05 resolution
round turned on.

THE FETCH IS THE PLATFORM'S OWN
===============================
``safe_fetch_document`` with the publisher's host as the allowlist, so the HTTPS-only
check, the internal-host guard, DNS pinning, the guarded redirect chain and the byte cap
all apply unchanged. A statistical API is still the open internet.

OFF BY DEFAULT
==============
``V3_MACRO_SOURCES_ENABLED`` is False. With it off nothing here makes a network call and
``fetch_series`` returns ``not_configured`` — an honest state, not an empty series.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.macro.sources import (
    FETCH_NOT_CONFIGURED,
    FETCH_UNREACHABLE,
    WORLD_BANK_HOST,
    SeriesFetch,
    parse_world_bank_payload,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: The Bank caps a page at 32,500; this is a research bound, not the API's.
MAX_PER_PAGE = 200


def build_url(indicator: str, geography: str, *, per_page: int) -> str:
    """The request URL. Indicator and geography are constrained by the caller's
    validation before they reach here — see ``_safe_token``."""
    return (
        f"https://{WORLD_BANK_HOST}/v2/country/{geography}/indicator/{indicator}"
        f"?format=json&per_page={per_page}"
    )


def _safe_token(value: str) -> str | None:
    """An indicator or country code, or ``None``.

    The World Bank's own codes are alphanumerics with dots and hyphens. Anything else is
    refused rather than escaped: this string becomes a URL path segment, and a path
    segment built by escaping is one encoding bug away from a different request.
    """
    text = (value or "").strip()
    if not text or len(text) > 40:
        return None
    if not all(ch.isalnum() or ch in ".-_" for ch in text):
        return None
    return text


@dataclass
class WorldBankMacroSource:
    """The live adapter. Fetches through the platform's guarded fetcher."""

    source_id: str = "world_bank_indicators"
    cfg: "Settings | None" = None
    #: Injected so a test can supply bytes without a network call, and so nothing can
    #: quietly substitute an unguarded fetch.
    fetcher: Any = None

    async def fetch_series(
        self, *, indicator: str, geography: str, limit: int = 60
    ) -> SeriesFetch:
        from app.core.config import settings as default_settings

        cfg = self.cfg or default_settings
        if not getattr(cfg, "v3_macro_sources_enabled", False):
            return SeriesFetch(
                status=FETCH_NOT_CONFIGURED,
                detail="V3_MACRO_SOURCES_ENABLED is off; no request was made.",
            )
        code = _safe_token(indicator)
        country = _safe_token(geography)
        if code is None or country is None:
            return SeriesFetch(
                status=FETCH_UNREACHABLE,
                detail=(
                    "indicator and geography must be World Bank codes "
                    "(alphanumerics, dots, hyphens). Refused rather than escaped: this "
                    "becomes a URL path segment."
                ),
            )
        per_page = max(1, min(int(limit), MAX_PER_PAGE))
        url = build_url(code, country, per_page=per_page)

        fetch = self.fetcher
        if fetch is None:
            from app.services.sources.document_fetcher import safe_fetch_document

            fetch = safe_fetch_document
        result = await fetch(
            url, allowed_domains=(WORLD_BANK_HOST,), cfg=cfg, resolve_ip=True
        )
        if getattr(result, "blocked", False) or not getattr(result, "ok", False):
            return SeriesFetch(
                status=FETCH_UNREACHABLE,
                detail=(
                    f"World Bank request did not return a usable response"
                    f"{': ' + result.error if getattr(result, 'error', None) else ''}"
                ),
            )
        content = getattr(result, "content", None) or b""
        return parse_world_bank_payload(
            content.decode("utf-8", "replace"),
            indicator=code,
            geography=country,
            source_ref=url,
        )


__all__ = ["MAX_PER_PAGE", "WorldBankMacroSource", "build_url"]
