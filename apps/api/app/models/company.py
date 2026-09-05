import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(sa.String(100))
    region: Mapped[str | None] = mapped_column(sa.String(100))
    sector: Mapped[str | None] = mapped_column(sa.String(100))
    industry: Mapped[str | None] = mapped_column(sa.String(100))
    market_cap: Mapped[float | None] = mapped_column(sa.Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(sa.String(10))
    website: Mapped[str | None] = mapped_column(sa.String(500))
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="new")
    #: The entity-master link (V3.2 Slice 2.2, migration 027). NULLABLE and it stays
    #: that way: NULL means no legal entity has been established for this company,
    #: which happens both before the backfill runs and permanently when two rows
    #: derive one entity key with different names — the backfill leaves the second
    #: unlinked rather than attributing it to the first row's entity.
    #:
    #: ``SET NULL``, not ``CASCADE``. Every FK *inside* the entity master is
    #: composition and cascades; this one is lineage, and deleting an entity must
    #: never delete the company row a thousand reports point at (CLAUDE.md rule 15).
    #:
    #: ``companies`` is otherwise untouched by V3: ``UNIQUE (ticker, exchange)``
    #: remains, every existing ``company_id`` FK keeps resolving, and nothing reads
    #: this column unless ``V3_ENTITY_MASTER_ENABLED`` is on.
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_companies_legal_entity_id_legal_entities",
        ),
        nullable=True,
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
        sa.Index("ix_companies_ticker", "ticker"),
        sa.Index("ix_companies_exchange", "exchange"),
        sa.Index("ix_companies_status", "status"),
        # "Which companies map to this entity?" — the reverse lookup the backfill's
        # de-duplication and slice 2.3's resolver both need.
        sa.Index("ix_companies_legal_entity_id", "legal_entity_id"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_companies_ticker_exchange"),
    )
