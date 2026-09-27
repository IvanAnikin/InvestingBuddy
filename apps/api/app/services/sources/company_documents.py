"""Which extracted documents belong to a company — V3.19.14.

An ``ExtractedDocument`` is shared: it is keyed by content hash and REUSED by every run
that fetches the same bytes, so its ``company_id`` is whichever run extracted it first
(and may be empty on older rows). The platform's own provenance link is the ingestion
attempt: ``DocumentIngestionAttempt(company_id, content_hash)``.

A company's documents: those it owns (``company_id``), plus UNOWNED ones (``company_id``
empty) that it fetched itself. A document already owned by ANOTHER company is never
borrowed through an attempt — a subsidiary whose IR page links its parent's annual
report does not acquire the parent's accounts.
"""

from __future__ import annotations

from typing import Any


def company_documents_clause(company_id: Any) -> Any:
    """A SQLAlchemy condition on ``ExtractedDocument`` selecting this company's documents."""
    from sqlalchemy import and_, or_, select

    from app.models.document_ingestion_attempt import DocumentIngestionAttempt
    from app.models.extracted_document import ExtractedDocument

    attempted = select(DocumentIngestionAttempt.content_hash).where(
        DocumentIngestionAttempt.company_id == company_id,
        DocumentIngestionAttempt.content_hash.is_not(None),
    )
    return or_(
        ExtractedDocument.company_id == company_id,
        and_(ExtractedDocument.company_id.is_(None),
             ExtractedDocument.content_hash.in_(attempted)),
    )


__all__ = ["company_documents_clause"]
