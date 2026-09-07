"""The raw-artifact lineage row — V3.1 Slice 1.1.

One row per distinct set of retrieved bytes, keyed by their SHA-256. This is the
*bottom* of the evidence stack:

    raw artifact ≠ parsed document ≠ evidence ≠ fact ≠ calculation ≠ finding

and it holds nothing about issuers, periods or scopes. Which company a document
belongs to, what period it covers and what it says are all properties of the
*document* built on top of it (Slice 1.2) — the same bytes can legitimately serve
two documents, and a table that conflates the two cannot represent that.

BYTES ARE OPTIONAL; LINEAGE IS NOT
==================================
``storage_key`` is nullable and its NULL has one precise meaning: **no bytes are
retained**, either because policy forbade storing them or because a retention
sweep removed them. ``storage_backend`` is never NULL — it is ``'none'`` in that
case — so a reader can always tell "deliberately not retained" from "written by
code that forgot to record where". The row itself always survives, because the
citation must keep resolving to the canonical URL even when the bytes are gone.

POLICY TRAVELS WITH THE ARTIFACT
================================
The six permissions of ``docs/v3/SECURITY_DATA_GOVERNANCE_AND_LICENSING.md`` §6
are columns, not a JSON blob, because they are queried: "which artifacts may be
indexed" and "which may go to an external model" are filters a later tool
boundary applies per document, and a filter over a JSONB key is exactly the kind
of thing that quietly stops being applied.

``retention_expires_at`` is nullable and NULL means "no TTL configured"
(OPEN DECISION #12 is still open and user-owned), never "expired" and never
"keep forever as a decision".
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchArtifact(Base):
    """Raw retrieved bytes, addressed by content hash."""

    __tablename__ = "research_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: SHA-256 hex of the RAW bytes. The identity — unique index below. The same
    #: value ``ExtractedDocument.content_hash`` already carries, deliberately, so
    #: the two records join without a second hash.
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    #: 'none' | 'memory' | 'local' | 'azure_blob'. Never NULL: see the module note.
    storage_backend: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    #: NULL means the bytes are NOT retained. Never a URL, never a secret — the
    #: key scheme is pure hex by construction (``corpus.artifacts.keys``).
    storage_key: Mapped[str | None] = mapped_column(sa.String(200))
    #: Governance §5 data class. Drives every permission below.
    access_class: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    #: Governance §6, one column each — queried as filters, never as a blob.
    policy_stored: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    policy_indexed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    policy_external_model: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )
    #: 'full' | 'bounded' | 'none'.
    policy_quoted: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    policy_retained_long_term: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )
    #: NULL = no TTL configured (OPEN DECISION #12), never "expired".
    retention_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: When the bytes were written. NULL when they never were.
    stored_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: When a retention sweep removed the bytes. The row stays; the lineage stays;
    #: only the bytes go. Set once and never cleared, so "we used to hold this"
    #: remains answerable.
    bytes_deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    #: How many times these exact bytes have been re-encountered. A deduplication
    #: counter is the cheapest available answer to "is re-fetching the same
    #: document the dominant cost of a run", which is a question V3.0's
    #: consumption telemetry can otherwise only guess at.
    seen_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index("ix_research_artifacts_content_hash", "content_hash", unique=True),
        # "What is due for expiry?" is the only scheduled question this table
        # will ever be asked, and it must not become a sequential scan of every
        # artifact the platform has ever held.
        sa.Index("ix_research_artifacts_retention_expires_at", "retention_expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchArtifact {self.content_hash[:12]} {self.storage_backend}>"


__all__ = ["ResearchArtifact"]
