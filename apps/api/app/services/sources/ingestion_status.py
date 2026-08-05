"""
The closed vocabularies for primary-document ingestion — Phase 32A Slice 5B.1.

Slice 5A had no durable record of a FAILED ingestion attempt: the persistence
service wrote a row only for a fully extracted document, so seven issuers'
attempts left ``extracted_documents`` at zero with nothing explaining why. Slice
5B.1 persists every attempt — which means an attacker-influenced or provider-
supplied string could otherwise reach the database and the admin UI.

This module is the single source of truth that prevents that. Both the fetch/
extraction layer (which produces these values) and the persistence layer (which
stores them) import from here, so the two can never drift, and anything outside
the vocabulary is coerced to ``unknown`` rather than stored verbatim.

Deliberately dependency-free: pure constants, no imports, no I/O. That is what
lets a model module import it without inverting the service/model layering.

Never encode into a failure code: a provider exception message, a URL, a query
string, a credential, a hostname, or an IP address. Codes are a fixed enum.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Attempt status — where an ingestion attempt actually got to.
# --------------------------------------------------------------------------- #

ATTEMPT_DISCOVERED = "discovered"
ATTEMPT_FETCHED = "fetched"
ATTEMPT_EXTRACTED = "extracted"
ATTEMPT_METADATA_ONLY = "metadata_only"
ATTEMPT_UNSUPPORTED = "unsupported"
ATTEMPT_ENCRYPTED = "encrypted"
ATTEMPT_PASSWORD_PROTECTED = "password_protected"
ATTEMPT_MALFORMED = "malformed"
ATTEMPT_REJECTED_SECURITY = "rejected_security"
ATTEMPT_TIMEOUT = "timeout"
ATTEMPT_EXTRACTION_FAILED = "extraction_failed"

ALL_ATTEMPT_STATUSES: tuple[str, ...] = (
    ATTEMPT_DISCOVERED,
    ATTEMPT_FETCHED,
    ATTEMPT_EXTRACTED,
    ATTEMPT_METADATA_ONLY,
    ATTEMPT_UNSUPPORTED,
    ATTEMPT_ENCRYPTED,
    ATTEMPT_PASSWORD_PROTECTED,
    ATTEMPT_MALFORMED,
    ATTEMPT_REJECTED_SECURITY,
    ATTEMPT_TIMEOUT,
    ATTEMPT_EXTRACTION_FAILED,
)

# --------------------------------------------------------------------------- #
# Failure code — WHY an attempt did not reach `extracted`. Sanitized by
# construction: a fixed enum, never provider text.
# --------------------------------------------------------------------------- #

FAILURE_BLOCKED_HOST = "blocked_host"
FAILURE_BLOCKED_SCHEME = "blocked_scheme"
FAILURE_BLOCKED_PRIVATE_IP = "blocked_private_ip"
FAILURE_BLOCKED_REDIRECT = "blocked_redirect"
FAILURE_REDIRECT_LIMIT = "redirect_limit"
FAILURE_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
FAILURE_HTTP_CLIENT_ERROR = "http_client_error"
FAILURE_HTTP_SERVER_ERROR = "http_server_error"
FAILURE_FETCH_TIMEOUT = "fetch_timeout"
FAILURE_EXTRACTION_TIMEOUT = "extraction_timeout"
FAILURE_NOT_A_PDF = "not_a_pdf"
FAILURE_ENCRYPTED_PDF = "encrypted_pdf"
FAILURE_PASSWORD_PROTECTED_PDF = "password_protected_pdf"
FAILURE_MALFORMED_PDF = "malformed_pdf"
FAILURE_SCANNED_NO_TEXT = "scanned_no_text"
FAILURE_EMPTY_EXTRACTION = "empty_extraction"
FAILURE_BUDGET_EXHAUSTED = "budget_exhausted"
FAILURE_CLIENT_UNAVAILABLE = "client_unavailable"
FAILURE_UNKNOWN = "unknown"

# Preflight identity-resolution failures — Phase 32A Slice 5B.1 hotfix. These are
# NOT fetch/extraction failures: they happen BEFORE any network request is made,
# when a real candidate (a known SEC filing) cannot be safely turned into a
# fetchable URL at all. Staging proved this class of failure was previously
# invisible: ``CompanyContext.cik`` is always None (``company_identity`` carries
# no ``cik`` field), so filer-identity resolution failed SILENTLY — no log, no
# gap, no attempt row, indistinguishable from "never ran".
FAILURE_MISSING_CIK = "missing_cik"
FAILURE_CONFLICTING_CIK = "conflicting_cik"
FAILURE_MALFORMED_ACCESSION = "malformed_accession"
FAILURE_INVALID_SEC_URL = "invalid_sec_url"
FAILURE_NO_PRIMARY_FILING_DOCUMENT = "no_primary_filing_document"
FAILURE_PREFLIGHT_BUDGET_EXHAUSTED = "preflight_budget_exhausted"

ALL_FAILURE_CODES: tuple[str, ...] = (
    FAILURE_BLOCKED_HOST,
    FAILURE_BLOCKED_SCHEME,
    FAILURE_BLOCKED_PRIVATE_IP,
    FAILURE_BLOCKED_REDIRECT,
    FAILURE_REDIRECT_LIMIT,
    FAILURE_UNSUPPORTED_CONTENT_TYPE,
    FAILURE_HTTP_CLIENT_ERROR,
    FAILURE_HTTP_SERVER_ERROR,
    FAILURE_FETCH_TIMEOUT,
    FAILURE_EXTRACTION_TIMEOUT,
    FAILURE_NOT_A_PDF,
    FAILURE_ENCRYPTED_PDF,
    FAILURE_PASSWORD_PROTECTED_PDF,
    FAILURE_MALFORMED_PDF,
    FAILURE_SCANNED_NO_TEXT,
    FAILURE_EMPTY_EXTRACTION,
    FAILURE_BUDGET_EXHAUSTED,
    FAILURE_CLIENT_UNAVAILABLE,
    FAILURE_MISSING_CIK,
    FAILURE_CONFLICTING_CIK,
    FAILURE_MALFORMED_ACCESSION,
    FAILURE_INVALID_SEC_URL,
    FAILURE_NO_PRIMARY_FILING_DOCUMENT,
    FAILURE_PREFLIGHT_BUDGET_EXHAUSTED,
    FAILURE_UNKNOWN,
)


def sanitize_failure_code(code: str | None) -> str:
    """Return ``code`` only when it is in the closed vocabulary, else ``unknown``.

    This is the guarantee that a raw provider exception string, a URL fragment or
    an IP address can never reach the database or the admin UI through the
    failure-code field. Callers may pass anything; only an enum member survives.
    """
    if code and code in ALL_FAILURE_CODES:
        return code
    return FAILURE_UNKNOWN


def is_attempt_status(status: str | None) -> bool:
    """True when ``status`` is a member of the closed attempt-status vocabulary."""
    return bool(status) and status in ALL_ATTEMPT_STATUSES


def http_status_class(code: int | None) -> str | None:
    """Bucket an HTTP status into ``2xx``/``3xx``/``4xx``/``5xx``.

    The exact status code is deliberately NOT retained: the bucket is enough for
    operational triage, and it keeps a provider's precise response out of stored
    telemetry.
    """
    if code is None:
        return None
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return None


def failure_code_for_block(reason: str | None) -> str:
    """Map a fetch-guard block reason onto a sanitized failure code.

    The guard's reason strings are code-defined (not provider text), but they are
    free-form and can embed a hostname or address, so they are never stored
    directly — this collapses them onto the enum.
    """
    text = (reason or "").lower()
    if "resolved ip" in text or "non-public ip" in text or "metadata ip" in text:
        return FAILURE_BLOCKED_PRIVATE_IP
    if "redirect" in text:
        return FAILURE_BLOCKED_REDIRECT
    if "scheme" in text:
        return FAILURE_BLOCKED_SCHEME
    if "allowlist" in text or "host" in text:
        return FAILURE_BLOCKED_HOST
    if "timeout" in text or "timed out" in text:
        return FAILURE_FETCH_TIMEOUT
    return FAILURE_UNKNOWN


def failure_code_for_exception(exc: BaseException) -> str:
    """Map an exception TYPE (never its message) onto a sanitized failure code."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return FAILURE_FETCH_TIMEOUT
    return FAILURE_UNKNOWN


# --------------------------------------------------------------------------- #
# Extraction status → attempt status.
#
# The extraction layer keeps its established three-value vocabulary
# (``extracted`` / ``metadata_only`` / ``extraction_failed``) because the council
# summary and the persistence path already count on it. The richer distinction
# the operator actually needs — was the document scanned, encrypted, password-
# protected or simply malformed? — is carried by the failure code and resolved
# into the attempt vocabulary here, at the persistence boundary.
# --------------------------------------------------------------------------- #

_FAILURE_TO_ATTEMPT_STATUS: dict[str, str] = {
    FAILURE_ENCRYPTED_PDF: ATTEMPT_ENCRYPTED,
    FAILURE_PASSWORD_PROTECTED_PDF: ATTEMPT_PASSWORD_PROTECTED,
    FAILURE_MALFORMED_PDF: ATTEMPT_MALFORMED,
    FAILURE_NOT_A_PDF: ATTEMPT_UNSUPPORTED,
    FAILURE_UNSUPPORTED_CONTENT_TYPE: ATTEMPT_UNSUPPORTED,
    FAILURE_SCANNED_NO_TEXT: ATTEMPT_METADATA_ONLY,
    FAILURE_BLOCKED_HOST: ATTEMPT_REJECTED_SECURITY,
    FAILURE_BLOCKED_SCHEME: ATTEMPT_REJECTED_SECURITY,
    FAILURE_BLOCKED_PRIVATE_IP: ATTEMPT_REJECTED_SECURITY,
    FAILURE_BLOCKED_REDIRECT: ATTEMPT_REJECTED_SECURITY,
    FAILURE_REDIRECT_LIMIT: ATTEMPT_REJECTED_SECURITY,
    FAILURE_FETCH_TIMEOUT: ATTEMPT_TIMEOUT,
    FAILURE_EXTRACTION_TIMEOUT: ATTEMPT_TIMEOUT,
    # Preflight failures (candidate known, but never reached a network fetch).
    # Mapped onto EXISTING attempt statuses only — no new status is introduced.
    #   * missing_cik / no_primary_filing_document: real filing metadata exists
    #     (form/accession/date) even though the fetchable document does not —
    #     the same "we know something, but not the content" shape as a scanned
    #     PDF, so it is honestly ``metadata_only``.
    #   * conflicting_cik / invalid_sec_url: an identity or URL failed a safety
    #     check, so this fails CLOSED exactly like a blocked host or private IP —
    #     ``rejected_security``.
    #   * malformed_accession: a malformed identifier we cannot act on —
    #     ``unsupported``, the same bucket a not-a-PDF / unsupported content type
    #     already uses for "well-formed input, unusable shape".
    #   * preflight_budget_exhausted: time ran out before this candidate's own
    #     fetch could even start — ``timeout``, alongside fetch/extraction
    #     timeouts.
    FAILURE_MISSING_CIK: ATTEMPT_METADATA_ONLY,
    FAILURE_NO_PRIMARY_FILING_DOCUMENT: ATTEMPT_METADATA_ONLY,
    FAILURE_CONFLICTING_CIK: ATTEMPT_REJECTED_SECURITY,
    FAILURE_INVALID_SEC_URL: ATTEMPT_REJECTED_SECURITY,
    FAILURE_MALFORMED_ACCESSION: ATTEMPT_UNSUPPORTED,
    FAILURE_PREFLIGHT_BUDGET_EXHAUSTED: ATTEMPT_TIMEOUT,
}


def attempt_status_for(
    extraction_status: str | None, failure_code: str | None
) -> str:
    """Resolve an extraction outcome into the durable attempt-status vocabulary.

    A successful extraction is always ``extracted``. Otherwise the sanitized
    failure code decides, so an encrypted document is recorded as ``encrypted``
    rather than collapsing into a generic failure — which is exactly the
    distinction Slice 5A could not make when every CFR document reported the same
    opaque ``extraction_failed``.
    """
    if extraction_status == ATTEMPT_EXTRACTED:
        return ATTEMPT_EXTRACTED
    code = sanitize_failure_code(failure_code)
    mapped = _FAILURE_TO_ATTEMPT_STATUS.get(code)
    if mapped:
        return mapped
    if extraction_status == ATTEMPT_METADATA_ONLY:
        return ATTEMPT_METADATA_ONLY
    return ATTEMPT_EXTRACTION_FAILED


__all__ = [
    "ALL_ATTEMPT_STATUSES",
    "ALL_FAILURE_CODES",
    "ATTEMPT_DISCOVERED",
    "ATTEMPT_ENCRYPTED",
    "ATTEMPT_EXTRACTED",
    "ATTEMPT_EXTRACTION_FAILED",
    "ATTEMPT_FETCHED",
    "ATTEMPT_MALFORMED",
    "ATTEMPT_METADATA_ONLY",
    "ATTEMPT_PASSWORD_PROTECTED",
    "ATTEMPT_REJECTED_SECURITY",
    "ATTEMPT_TIMEOUT",
    "ATTEMPT_UNSUPPORTED",
    "FAILURE_BLOCKED_HOST",
    "FAILURE_BLOCKED_PRIVATE_IP",
    "FAILURE_BLOCKED_REDIRECT",
    "FAILURE_BLOCKED_SCHEME",
    "FAILURE_BUDGET_EXHAUSTED",
    "FAILURE_CLIENT_UNAVAILABLE",
    "FAILURE_CONFLICTING_CIK",
    "FAILURE_EMPTY_EXTRACTION",
    "FAILURE_ENCRYPTED_PDF",
    "FAILURE_EXTRACTION_TIMEOUT",
    "FAILURE_FETCH_TIMEOUT",
    "FAILURE_HTTP_CLIENT_ERROR",
    "FAILURE_HTTP_SERVER_ERROR",
    "FAILURE_INVALID_SEC_URL",
    "FAILURE_MALFORMED_ACCESSION",
    "FAILURE_MALFORMED_PDF",
    "FAILURE_MISSING_CIK",
    "FAILURE_NOT_A_PDF",
    "FAILURE_NO_PRIMARY_FILING_DOCUMENT",
    "FAILURE_PASSWORD_PROTECTED_PDF",
    "FAILURE_PREFLIGHT_BUDGET_EXHAUSTED",
    "FAILURE_REDIRECT_LIMIT",
    "FAILURE_SCANNED_NO_TEXT",
    "FAILURE_UNKNOWN",
    "FAILURE_UNSUPPORTED_CONTENT_TYPE",
    "attempt_status_for",
    "failure_code_for_block",
    "failure_code_for_exception",
    "http_status_class",
    "is_attempt_status",
    "sanitize_failure_code",
]
