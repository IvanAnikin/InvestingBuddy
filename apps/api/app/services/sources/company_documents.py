"""Which extracted documents belong to a company — V3.19.14.

An ``ExtractedDocument`` is shared: it is keyed by content hash and REUSED by every run
that fetches the same bytes, so its ``company_id`` is whichever run extracted it first
(and may be empty on older rows). The platform's own provenance link is the ingestion
attempt: ``DocumentIngestionAttempt(company_id, content_hash)``. A company's documents
are the union of both, so a reused document is never missed and never borrowed by
another company (an attempt is always made for one company).
"""

from __future__ import annotations

from typing import Any


def company_documents_clause(company_id: Any) -> Any:
    """A SQLAlchemy condition on ``ExtractedDocument`` selecting this company's documents."""
    from sqlalchemy import or_, select

    from app.models.document_ingestion_attempt import DocumentIngestionAttempt
    from app.models.extracted_document import ExtractedDocument

    attempted = select(DocumentIngestionAttempt.content_hash).where(
        DocumentIngestionAttempt.company_id == company_id,
        DocumentIngestionAttempt.content_hash.is_not(None),
    )
    return or_(
        ExtractedDocument.company_id == company_id,
        ExtractedDocument.content_hash.in_(attempted),
    )


__all__ = ["company_documents_clause"]
