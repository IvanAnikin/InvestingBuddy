"""Persist an honest record of EVERY primary-document ingestion attempt —
Phase 32A Slice 5B.1.

Slice 5A's writer (``extracted_document_service.persist_primary_document_
artifacts``) only ever wrote a row when an artifact reached ``status ==
'extracted'``. Every FAILED attempt persisted nothing, so a staging run that
tried documents across seven issuers left ``extracted_documents`` /
``extracted_facts`` at 0/0 with no durable record of what was tried or why it
failed. This module is that record.

Design invariants (product safety):
  * Gated behind BOTH ``primary_document_ingestion_enabled`` and
    ``report_citation_persistence_enabled``. With either flag OFF these
    functions issue NO query and write NO row, returning immediately
    (byte-identical dark path).
  * Flush-only — the caller owns the single commit (mirrors the Slice-3 /
    Slice-5A writers).
  * Never raises on record content: a telemetry record must never break the
    ingestion path it observes, so a malformed / unrecognised record is skipped
    or downgraded, never stored raw. (DB-level session errors still propagate to
    the caller, which — exactly as for the Slice-5A writer — isolates this write
    in a SAVEPOINT; swallowing them would leave the session unusable.)
  * Idempotent per ``(company_id, agent_run_id, url_hash)``: re-attempting the
    same URL in the same run UPDATES the existing row in place. A pre-query
    backs the UNIQUE constraint because PostgreSQL NULLs never collide inside a
    UNIQUE constraint (NULL company / run).

NEVER persisted here:
  * raw provider or exception text (``failure_code`` is a CLOSED vocabulary —
    anything else becomes ``unknown``);
  * secrets, API tokens or signed query strings (the URL is canonicalized +
    credential-stripped before it is hashed or stored);
  * exact HTTP status codes (only the class ``2xx`` / ``3xx`` / ``4xx`` /
    ``5xx``);
  * document bodies, extracted text, excerpts or OCR output;
  * any financial number, price target, valuation or recommendation.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_ingestion_attempt import (
    ALL_STATUSES,
    DocumentIngestionAttempt,
)

# ``sanitize_failure_code`` / ``http_status_class`` are defined ONCE, in the
# module that also defines the closed vocabularies, and are re-exported from here
# so the writer and the fetch/extraction layer can never drift apart.
from app.services.sources.ingestion_status import (
    http_status_class,
    sanitize_failure_code,
)
from app.services.sources.redaction import canonicalize_source_url, strip_url_secrets

if TYPE_CHECKING:  # avoid a runtime import cycle — attributes are read by name
    from collections.abc import Sequence

    from app.core.config import Settings

# Defensive column-length guards — truncating keeps a pathological URL / value
# from failing the INSERT (an attempt record must never break ingestion).
_CANONICAL_URL_MAX = 2000
_URL_HASH_MAX = 64
_SOURCE_TYPE_MAX = 50
_SOURCE_TIER_MAX = 50
_DOC_KIND_MAX = 50
_DISCOVERY_STRATEGY_MAX = 50
_STATUS_MAX = 50
_FAILURE_CODE_MAX = 50
_MIME_TYPE_MAX = 100
_HTTP_STATUS_CLASS_MAX = 10
_EXTRACTION_METHOD_MAX = 50
_CONTENT_HASH_MAX = 64

_STATUS_SET = frozenset(ALL_STATUSES)


@dataclass
class IngestionAttemptRecord:
    """One bounded, secret-free ingestion attempt to persist."""

    canonical_url: str
    source_type: str
    source_tier: str
    status: str
    doc_kind: str | None = None
    discovery_strategy: str | None = None
    failure_code: str | None = None
    mime_type: str | None = None
    http_status_class: str | None = None
    extraction_method: str | None = None
    page_count: int | None = None
    content_hash: str | None = None
    fetch_ms: int | None = None
    extraction_ms: int | None = None
    total_ms: int | None = None
    # Tri-state: True = the connection was pinned to a pre-validated address
    # (ADR-014/015); False = an honest "not pinned"; None = no fetch happened.
    pinned: bool | None = None


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _canonical(url: str | None) -> str:
    """Canonical, credential-free form of ``url`` (never raises)."""
    raw = url or ""
    return canonicalize_source_url(raw) or strip_url_secrets(raw) or raw


def url_hash_of(url: str) -> str:
    """sha256 of the CANONICALIZED url — signed variants hash identically."""
    return hashlib.sha256(_canonical(url).encode("utf-8", "replace")).hexdigest()


def _gates_open(cfg: "Settings | None") -> bool:
    """BOTH flags must be on. Either OFF ⇒ no query, no row (dark path)."""
    if cfg is None:
        from app.core.config import settings as _settings

        cfg = _settings
    return bool(
        getattr(cfg, "primary_document_ingestion_enabled", False)
        and getattr(cfg, "report_citation_persistence_enabled", False)
    )


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: object) -> bool | None:
    """Only a real bool is stored; anything else is an honest NULL ('unknown')."""
    return value if isinstance(value, bool) else None


def _apply(
    row: DocumentIngestionAttempt,
    *,
    record: IngestionAttemptRecord,
    canonical: str,
    url_hash: str,
    now: datetime,
) -> None:
    """Write the (already validated + sanitized) record onto ``row``."""
    row.canonical_url = _clip(canonical, _CANONICAL_URL_MAX) or ""
    row.url_hash = _clip(url_hash, _URL_HASH_MAX) or ""
    row.source_type = _clip(record.source_type or "", _SOURCE_TYPE_MAX) or ""
    row.source_tier = _clip(record.source_tier or "", _SOURCE_TIER_MAX) or ""
    row.doc_kind = _clip(record.doc_kind, _DOC_KIND_MAX)
    row.discovery_strategy = _clip(
        record.discovery_strategy, _DISCOVERY_STRATEGY_MAX
    )
    row.attempted_at = now
    row.status = _clip(record.status, _STATUS_MAX) or ""
    # A blank/absent failure code means "no failure" — only a REPORTED failure is
    # sanitized (an unrecognised one becomes ``unknown``, never raw text).
    row.failure_code = (
        _clip(sanitize_failure_code(record.failure_code), _FAILURE_CODE_MAX)
        if (record.failure_code or "").strip()
        else None
    )
    row.mime_type = _clip(record.mime_type, _MIME_TYPE_MAX)
    row.http_status_class = _clip(record.http_status_class, _HTTP_STATUS_CLASS_MAX)
    row.extraction_method = _clip(record.extraction_method, _EXTRACTION_METHOD_MAX)
    row.page_count = _int_or_none(record.page_count)
    row.content_hash = _clip(record.content_hash, _CONTENT_HASH_MAX)
    row.fetch_ms = _int_or_none(record.fetch_ms)
    row.extraction_ms = _int_or_none(record.extraction_ms)
    row.total_ms = _int_or_none(record.total_ms)
    row.pinned = _bool_or_none(record.pinned)
    row.updated_at = now


async def record_ingestion_attempts(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    attempts: "Sequence[IngestionAttemptRecord] | None",
    cfg: "Settings | None" = None,
) -> int:
    """Persist one row per attempt — including the FAILED ones — idempotently.

    Flush-only: the caller owns the single commit. Returns the number of rows
    written or updated. With either gate flag OFF, or with no attempts, returns
    ``0`` WITHOUT issuing any query (dark path).

    An attempt whose ``status`` is outside the CLOSED vocabulary is SKIPPED
    entirely rather than stored as junk; a failure code outside the CLOSED
    vocabulary is downgraded to ``unknown``.
    """
    if not _gates_open(cfg):
        return 0
    if not attempts:
        return 0

    now = _utcnow()
    written = 0
    # Rows written during THIS call, so a duplicated URL inside one batch updates
    # the row we just added rather than issuing a second INSERT.
    pending: dict[str, DocumentIngestionAttempt] = {}

    for record in attempts:
        try:
            status = (getattr(record, "status", "") or "").strip()
        except (AttributeError, TypeError):
            continue
        if status not in _STATUS_SET:
            # Unknown status ⇒ skip the row entirely (never write junk).
            continue

        canonical = _canonical(getattr(record, "canonical_url", "") or "")
        if not canonical:
            continue
        url_hash = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()

        row = pending.get(url_hash)
        if row is None:
            row = (
                await session.execute(
                    select(DocumentIngestionAttempt)
                    .where(
                        DocumentIngestionAttempt.company_id == company_id,
                        DocumentIngestionAttempt.agent_run_id == agent_run_id,
                        DocumentIngestionAttempt.url_hash == url_hash,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            row = DocumentIngestionAttempt(
                id=uuid.uuid4(),
                company_id=company_id,
                agent_run_id=agent_run_id,
                canonical_url="",
                url_hash=url_hash,
                source_type="",
                source_tier="",
                status=status,
                created_at=now,
            )
            session.add(row)

        try:
            _apply(
                row,
                record=record,
                canonical=canonical,
                url_hash=url_hash,
                now=now,
            )
        except (AttributeError, TypeError, ValueError):
            # Malformed record content never breaks ingestion telemetry.
            continue
        pending[url_hash] = row
        written += 1

    if written:
        await session.flush()
    return written


async def load_attempt_summary(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None = None,
    cfg: "Settings | None" = None,
) -> dict[str, int]:
    """Return bounded per-status attempt counts plus a ``total``.

    e.g. ``{"discovered": 3, "extracted": 1, "total": 4}``. Strictly scoped to
    ``company_id`` (and ``agent_run_id`` when given) — one company never sees
    another company's attempts. Gated behind BOTH flags: with either OFF, or with
    no ``company_id``, returns ``{}`` WITHOUT issuing any query (dark path).

    Secret-free and bounded by construction — counts only, never URLs, failure
    text or document content. This is what a later slice surfaces in the admin
    API.
    """
    if not _gates_open(cfg):
        return {}
    if company_id is None:
        return {}

    stmt = (
        select(DocumentIngestionAttempt.status, func.count())
        .where(DocumentIngestionAttempt.company_id == company_id)
        .group_by(DocumentIngestionAttempt.status)
    )
    if agent_run_id is not None:
        stmt = stmt.where(DocumentIngestionAttempt.agent_run_id == agent_run_id)

    summary: dict[str, int] = {}
    total = 0
    for status, count in (await session.execute(stmt)).all():
        value = int(count or 0)
        summary[str(status)] = value
        total += value
    summary["total"] = total
    return summary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "IngestionAttemptRecord",
    # Re-exported from ``ingestion_status`` (their single definition) so callers
    # of the writer keep one import site without a second implementation.
    "http_status_class",
    "load_attempt_summary",
    "record_ingestion_attempts",
    "sanitize_failure_code",
    "url_hash_of",
]
