"""Turn a primary-document artifact into a durable ingestion-attempt record —
Phase 32A Slice 5B.1.

The seam between the layer that ATTEMPTS an ingestion (``live_fetchers`` +
``CompanyIrConnector``) and the layer that RECORDS it
(``document_ingestion_attempt_service``). Slice 5A had no such seam: only a fully
``extracted`` artifact was ever written, so seven issuers' worth of failures left
``extracted_documents`` at zero with nothing explaining why. Every artifact —
successful or not — now maps onto exactly one honest attempt record.

Pure by design: no database session, no network, no I/O, no clock. That keeps it
trivially unit-testable and lets the caller decide when (and whether) to persist.

Safety properties:
  * ``status`` comes from ``attempt_status_for`` — the CLOSED attempt vocabulary —
    so a scanned document is ``metadata_only``, an encrypted one is ``encrypted``,
    and a blocked host is ``rejected_security`` rather than one opaque failure.
  * ``failure_code`` is sanitized here as well as at the writer, so a raw provider
    or exception string can never survive as far as the record. Anything outside
    the closed vocabulary becomes ``unknown``.
  * Only an HTTP status CLASS is carried; the exact code never is.
  * Nothing here reads or copies document text, excerpts, table cells, facts or
    any financial number — only counts, timings, hashes and closed-vocabulary
    labels.
  * Total and defensive: every field is read with ``getattr`` so a duck-typed or
    partially-populated artifact maps without raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.document_ingestion_attempt_service import IngestionAttemptRecord
from app.services.sources.ingestion_status import (
    attempt_status_for,
    sanitize_failure_code,
)
from app.services.sources.sec_filing_documents import STRATEGY_SEC_ACCESSION
from app.services.sources.taxonomy import T1_PRIMARY_FILING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

# Where an attempted document came from. Deliberately coarse: the fine-grained
# "how was it found" lives in ``discovery_strategy``.
SOURCE_TYPE_COMPANY_IR = "company_ir_primary_document"
SOURCE_TYPE_SEC_FILING = "sec_filing_document"

# Both paths retrieve an issuer's own primary disclosure document, so the CONTENT
# tier is the same T1 for each; they differ only in transport (issuer site vs SEC
# EDGAR), which the evidence items already record.
SOURCE_TIER_PRIMARY_FILING = T1_PRIMARY_FILING


def _int_or_none(value: Any) -> int | None:
    """Coerce a duck-typed numeric field to ``int``; anything odd becomes None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _total_ms(fetch_ms: int | None, extraction_ms: int | None) -> int | None:
    """Sum the phases that actually ran; None when neither did."""
    if fetch_ms is None and extraction_ms is None:
        return None
    return (fetch_ms or 0) + (extraction_ms or 0)


def artifact_to_attempt(
    artifact: Any, *, source_type: str, source_tier: str
) -> IngestionAttemptRecord:
    """Map ONE primary-document artifact onto one durable attempt record.

    The artifact's extraction status plus its sanitized failure code decide the
    attempt status, so a failure is recorded with WHY it failed rather than being
    silently dropped (the Slice 5A behaviour this replaces). Never raises.
    """
    extraction = getattr(artifact, "extraction", None)

    raw_failure = getattr(artifact, "failure_code", None)
    status = attempt_status_for(getattr(artifact, "status", None), raw_failure)
    # A reported failure is coerced onto the closed vocabulary; no reported
    # failure stays None ("nothing went wrong") rather than becoming 'unknown'.
    failure_code = sanitize_failure_code(raw_failure) if raw_failure else None

    fetch_ms = _int_or_none(getattr(artifact, "fetch_ms", None))
    extraction_ms = _int_or_none(getattr(artifact, "extraction_ms", None))

    # Was the connection pinned to a pre-validated address (ADR-014/015)? Kept
    # tri-state: True/False are honest outcomes of an attempted fetch, None means
    # no fetch was attempted (budget-exhausted / reused document).
    raw_pinned = getattr(artifact, "pinned", None)
    pinned = bool(raw_pinned) if isinstance(raw_pinned, bool) else None

    # Prefer the fetch layer's document bucket (pdf / html / text); fall back to
    # the extraction's own recorded mime type when the fetch never classified one.
    mime_type = getattr(artifact, "document_type", None) or (
        getattr(extraction, "mime_type", None) if extraction is not None else None
    )
    content_hash = getattr(artifact, "content_hash", None) or (
        getattr(extraction, "content_hash", None) if extraction is not None else None
    )

    return IngestionAttemptRecord(
        canonical_url=str(getattr(artifact, "source_url", "") or ""),
        source_type=source_type,
        source_tier=source_tier,
        status=status,
        doc_kind=getattr(artifact, "doc_kind", None),
        discovery_strategy=getattr(artifact, "discovery_strategy", None),
        failure_code=failure_code,
        mime_type=mime_type,
        http_status_class=getattr(artifact, "http_status_class", None),
        extraction_method=(
            getattr(extraction, "extraction_method", None)
            if extraction is not None
            else None
        ),
        page_count=_int_or_none(
            getattr(extraction, "page_count", None) if extraction is not None else None
        ),
        content_hash=content_hash,
        fetch_ms=fetch_ms,
        extraction_ms=extraction_ms,
        total_ms=_total_ms(fetch_ms, extraction_ms),
        pinned=pinned,
    )


def artifacts_to_attempts(
    artifacts: "Sequence[Any] | None", *, source_type: str, source_tier: str
) -> list[IngestionAttemptRecord]:
    """Map a batch of artifacts. ``None`` / empty ⇒ ``[]`` (no work, no record)."""
    if not artifacts:
        return []
    return [
        artifact_to_attempt(a, source_type=source_type, source_tier=source_tier)
        for a in artifacts
    ]


def attempts_for_primary_documents(
    artifacts: "Sequence[Any] | None",
) -> list[IngestionAttemptRecord]:
    """Map a run's mixed artifact list onto attempt records with honest labels.

    Issuer-IR and SEC filing-body artifacts arrive in one list, so each is
    attributed to the source it actually came from (identified by the SEC
    accession discovery strategy) instead of all being labelled company IR.
    """
    if not artifacts:
        return []
    out: list[IngestionAttemptRecord] = []
    for artifact in artifacts:
        is_sec = getattr(artifact, "discovery_strategy", None) == STRATEGY_SEC_ACCESSION
        out.append(
            artifact_to_attempt(
                artifact,
                source_type=SOURCE_TYPE_SEC_FILING if is_sec else SOURCE_TYPE_COMPANY_IR,
                source_tier=SOURCE_TIER_PRIMARY_FILING,
            )
        )
    return out


__all__ = [
    "SOURCE_TIER_PRIMARY_FILING",
    "SOURCE_TYPE_COMPANY_IR",
    "SOURCE_TYPE_SEC_FILING",
    "artifact_to_attempt",
    "artifacts_to_attempts",
    "attempts_for_primary_documents",
]
