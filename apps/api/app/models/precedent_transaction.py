"""Precedent transactions — V3.20 slice 8.3 (migration 056).

One row per acquisition of an electricity-stack business, used as a precedent multiple.
Every row is CITED (CLAUDE.md rule 6): a source URL, the announcement date, the currency
and the retrieval timestamp are required by the schema, not by convention.

Two ways in, one way out:

* ``entry_method = "extracted"`` — proposed by the deterministic press-release scan of a
  company's held documents (``services/valuation/precedents.scan_company_documents``);
* ``entry_method = "curated"`` — entered by an admin.

Every row starts ``status = "proposed"``. Only a human admin's approval (with the target
metric and the layer set) makes it ``approved``; only approved rows ever reach a report.
A rejected row is kept: what was rejected and why is learning data (CLAUDE.md rule 8).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUSES = ("proposed", "approved", "rejected")
ENTRY_METHODS = ("extracted", "curated")
METRIC_KEYS = ("ebit", "ebitda", "revenue", "capacity_mw")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PrecedentTransaction(Base):
    __tablename__ = "precedent_transactions"
    __table_args__ = (
        sa.UniqueConstraint("acquirer_name", "target_name", "announced_date",
                            name="uq_precedent_transactions_deal"),
        sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected')",
                           name="ck_precedent_transactions_status"),
        sa.CheckConstraint("entry_method IN ('extracted', 'curated')",
                           name="ck_precedent_transactions_entry_method"),
        sa.CheckConstraint("enterprise_value IS NULL OR enterprise_value > 0",
                           name="ck_precedent_transactions_ev_positive"),
        sa.CheckConstraint(
            "metric_key IS NULL OR metric_key IN ('ebit', 'ebitda', 'revenue', 'capacity_mw')",
            name="ck_precedent_transactions_metric_key"),
        sa.CheckConstraint(
            "status <> 'approved' OR (enterprise_value IS NOT NULL AND metric_key IS NOT NULL "
            "AND metric_value IS NOT NULL AND metric_value > 0 AND layer IS NOT NULL)",
            name="ck_precedent_transactions_approved_complete"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    acquirer_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    target_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    target_company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True)
    layer: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    announced_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    enterprise_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2), nullable=True)
    #: What the figure in the source is: "enterprise_value" or "consideration" (equity).
    value_basis: Mapped[str] = mapped_column(sa.String(30), nullable=False,
                                             default="enterprise_value")
    metric_key: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    metric_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(24, 2), nullable=True)
    metric_period: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    source_url: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    source_title: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                   nullable=False)
    entry_method: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="proposed")
    reviewed_by: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True),
                                                         nullable=True)
    review_note: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now,
                                                 nullable=False)

    @property
    def multiple(self) -> Decimal | None:
        if self.enterprise_value is None or not self.metric_value:
            return None
        return self.enterprise_value / self.metric_value


__all__ = ["ENTRY_METHODS", "METRIC_KEYS", "STATUSES", "PrecedentTransaction"]
