"""
Bounded, allowlisted, SSRF-safe *document* fetcher — Phase 29B.2.

Fetches ONE company-owned primary document (an issuer's annual report / universal
registration document / integrated report, as PDF, HTML or plain text) so its
text can be excerpted and its primary facts parsed. It is the document analogue
of ``safe_web_fetcher.safe_fetch_page`` (which fetches an IR *landing page* and
extracts links) and shares the exact same guards.

Safety properties (why this is not an SSRF surface):
  * **No arbitrary URL.** A URL only ever reaches this module from the
    code-defined ``verified_issuer_sources`` registry, or from an annual-report
    link already extracted from an allowlisted page by ``safe_web_fetcher`` — and
    every URL is re-checked against the issuer's ``allowed_domains`` before a
    request is made. The API never accepts a URL.
  * **HTTPS only.** ``http://`` and every other scheme is rejected.
  * **Host allowlist.** The host must be inside the issuer's ``allowed_domains``.
  * **No private / internal targets.** IP-literal / localhost / .internal hosts
    are rejected (belt-and-braces on top of the allowlist).
  * **Redirects are guarded, not followed blindly.** A redirect to a host outside
    the allowlist, or a downgrade to http, aborts the fetch.
  * **Content-type gated.** Only ``application/pdf`` / ``text/html`` /
    ``text/plain`` (config) are accepted; anything else degrades to a gap.
  * **Bounded.** Timeout + max-bytes are config-capped; a document larger than
    the cap is truncated, never fully buffered.
  * **No cookies / auth headers / tokenized URLs persisted.** Nothing here sends
    credentials, and the stored ``final_url`` is secret-stripped.
  * **Never raises.** Every failure degrades to a ``DocumentFetchResult`` with
    ``error`` / ``blocked`` set and an honest ``SourceGap``.
  * **Secret-free.** No prompts, bodies, or credentials are ever logged.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.ingestion_status import (
    FAILURE_BLOCKED_REDIRECT,
    FAILURE_CLIENT_UNAVAILABLE,
    FAILURE_HTTP_CLIENT_ERROR,
    FAILURE_HTTP_SERVER_ERROR,
    FAILURE_REDIRECT_LIMIT,
    FAILURE_UNSUPPORTED_CONTENT_TYPE,
    failure_code_for_block,
    failure_code_for_exception,
    http_status_class,
)
from app.services.sources.redaction import strip_url_secrets
from app.services.sources.safe_web_fetcher import (
    _USER_AGENT,
    Resolver,
    async_check_fetch_url,
    host_of,
    pinned_transport_for,
)

# Document type buckets the extractor understands, keyed by content-type prefix.
_PDF_TYPES = ("application/pdf", "application/x-pdf")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("text/plain",)


def _parse_allowed_content_types(cfg: Settings) -> tuple[str, ...]:
    raw = cfg.source_document_extraction_allowed_content_types or ""
    return tuple(t.strip().lower() for t in raw.split(",") if t.strip())


def classify_content_type(content_type: str | None, url: str | None = None) -> str | None:
    """Return ``pdf`` | ``html`` | ``text`` for a content-type, else None.

    Falls back to the URL extension only for PDFs (a ``.pdf`` served as a generic
    ``application/octet-stream`` is still a PDF); anything else must declare a
    recognised text/html/pdf content type.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if any(ct == t or ct.startswith(t) for t in _PDF_TYPES):
        return "pdf"
    if any(ct == t or ct.startswith(t) for t in _HTML_TYPES):
        return "html"
    if any(ct == t or ct.startswith(t) for t in _TEXT_TYPES):
        return "text"
    if url and url.lower().split("?")[0].endswith(".pdf"):
        return "pdf"
    return None


@dataclass
class DocumentFetchResult:
    """Everything one bounded document fetch produced. Never carries a secret."""

    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    document_type: str | None = None  # pdf | html | text
    content: bytes | None = None
    truncated: bool = False
    error: str | None = None
    blocked: bool = False
    warnings: list[str] = field(default_factory=list)
    source_gaps: list[SourceGap] = field(default_factory=list)
    # Phase 32A Slice 5B.1 — bounded, sanitized operational telemetry. These feed
    # the durable ingestion-attempt record; none of them can carry provider text,
    # a URL secret or an address.
    failure_code: str | None = None
    # True only when the connection was pinned to a pre-validated address. False
    # is an honest "not pinned", never a claim that pinning happened.
    pinned: bool = False

    @property
    def status_class(self) -> str | None:
        """``2xx``/``3xx``/``4xx``/``5xx`` — the exact code is never retained."""
        return http_status_class(self.status_code)

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and not self.blocked
            and self.content is not None
            and (self.status_code or 0) < 400
        )

    def _gap(self, message: str) -> None:
        self.source_gaps.append(
            SourceGap(
                connector_key="company_ir",
                source_id="company_ir",
                gap_type=GapType.primary_filing_unavailable,
                severity=GapSeverity.info,
                message=message,
                blocks_research_complete=False,
            )
        )


async def safe_fetch_document(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    cfg: Settings | None = None,
    resolve_ip: bool = False,
    resolver: Resolver = socket.getaddrinfo,
) -> DocumentFetchResult:
    """Fetch one allowlisted HTTPS document (bounded, guarded, never raising).

    Returns a ``DocumentFetchResult``. On any failure (blocked host, off-domain
    redirect, disallowed content type, timeout, http error) it degrades to a
    result with ``error``/``blocked`` set and an honest ``SourceGap`` — never a
    fabricated document.

    ``resolve_ip`` is OPT-IN (default OFF): when True the target host's resolved
    IPs are checked before the initial fetch AND after each redirect hop (closing
    the DNS-rebinding SSRF vector). Left OFF (the default) every existing caller
    is byte-for-byte unchanged. ``resolver`` is injectable for tests.
    """
    cfg = cfg or default_settings
    result = DocumentFetchResult(requested_url=strip_url_secrets(url) or url)

    reason, pinned_ip = await async_check_fetch_url(
        url, allowed_domains, cfg=cfg, resolve_ip=resolve_ip, resolver=resolver
    )
    if reason:
        result.blocked = True
        result.error = reason
        result.failure_code = failure_code_for_block(reason)
        result._gap(
            f"Annual-report document could not be safely fetched ({reason}); "
            "document text is not extracted."
        )
        return result

    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        result.error = f"http client unavailable: {type(exc).__name__}"
        result.failure_code = FAILURE_CLIENT_UNAVAILABLE
        result._gap("Document fetch skipped — HTTP client unavailable.")
        return result

    allowed_types = _parse_allowed_content_types(cfg)
    max_bytes = max(1, cfg.source_document_extraction_max_bytes)
    timeout = max(1, cfg.source_document_extraction_timeout_seconds)
    current = url
    # When an address was validated, connect ONLY to it (Slice 5B.1 pinning).
    transport = pinned_transport_for(cfg, host_of(url), pinned_ip)
    result.pinned = transport is not None
    client_kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": timeout,
        "cookies": None,
        "headers": {
            "User-Agent": _USER_AGENT,
            "Accept": "application/pdf,text/html,text/plain,*/*",
        },
    }
    if transport is not None:
        client_kwargs["transport"] = transport
    try:
        # No cookies, no auth, no Referer — a plain, credential-free document GET.
        async with httpx.AsyncClient(**client_kwargs) as client:
            for _hop in range(4):  # bounded redirect chain
                async with client.stream("GET", current) as resp:
                    result.status_code = resp.status_code
                    result.final_url = strip_url_secrets(current)
                    if resp.is_redirect:
                        location = resp.headers.get("location", "")
                        nxt = urljoin(current, location)
                        block, next_ip = await async_check_fetch_url(
                            nxt,
                            allowed_domains,
                            cfg=cfg,
                            resolve_ip=resolve_ip,
                            resolver=resolver,
                        )
                        if block:
                            result.blocked = True
                            result.error = f"redirect blocked ({block})"
                            result.failure_code = FAILURE_BLOCKED_REDIRECT
                            result._gap(
                                "Annual-report document redirected off the verified "
                                f"issuer domain ({block}); not fetched."
                            )
                            return result
                        # Re-pin the new hop against its own validated address.
                        if transport is not None and next_ip:
                            transport.pin(host_of(nxt), next_ip)
                        current = nxt
                        continue
                    if resp.status_code >= 400:
                        result.error = f"http {resp.status_code}"
                        result.failure_code = (
                            FAILURE_HTTP_SERVER_ERROR
                            if resp.status_code >= 500
                            else FAILURE_HTTP_CLIENT_ERROR
                        )
                        result._gap(
                            "Annual-report document could not be fetched "
                            f"(http {resp.status_code}); document text is not extracted."
                        )
                        return result

                    content_type = resp.headers.get("content-type")
                    result.content_type = content_type
                    doc_type = classify_content_type(content_type, current)
                    ct_prefix = (content_type or "").split(";")[0].strip().lower()
                    if doc_type is None or (
                        allowed_types
                        and ct_prefix
                        and not any(ct_prefix.startswith(t) for t in allowed_types)
                        and doc_type != "pdf"  # .pdf-by-extension is still allowed
                    ):
                        result.blocked = True
                        result.error = f"unsupported content-type: {ct_prefix or 'unknown'}"
                        result.failure_code = FAILURE_UNSUPPORTED_CONTENT_TYPE
                        result._gap(
                            "Annual-report link is not a supported document type "
                            f"({ct_prefix or 'unknown'}); document text is not extracted."
                        )
                        return result
                    result.document_type = doc_type

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= max_bytes:
                            result.truncated = True
                            break
                    result.content = b"".join(chunks)[:max_bytes]
                    if result.truncated:
                        result.warnings.append(
                            "Document exceeded the max-bytes cap and was truncated; "
                            "only the leading portion was read."
                        )
                    return result
            result.blocked = True
            result.error = "too many redirects"
            result.failure_code = FAILURE_REDIRECT_LIMIT
            result._gap("Annual-report document exceeded the redirect limit; not fetched.")
            return result
    except Exception as exc:  # noqa: BLE001 - fetch must never crash a run
        result.error = f"fetch failed: {type(exc).__name__}"
        result.failure_code = failure_code_for_exception(exc)
        result._gap(
            "Annual-report document could not be safely fetched "
            f"({type(exc).__name__}); document text is not extracted."
        )
        return result


__all__ = [
    "DocumentFetchResult",
    "safe_fetch_document",
    "classify_content_type",
]
